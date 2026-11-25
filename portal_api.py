"""The portal's own requests, without a Chrome window (operator, 30 Oct 2026, build 150).

What the portal's web page sends when a person clicks: its sign-in, the assignment, the upload
link, the file, the marks and the notification. Read from the page's code and checked against
evaluations the browser path saved (ravi.kumar18, 288 copies in review on 30 Oct 2026):
checkedFile is the key given to /file, questionsScore has one mark per question, rating is
the page's five tags at 0, and a sheet's submission id is the portal's row _id.

This is not a published API, so every write is guarded: the copy's row must be this student's
in this test before anything changes, and the result is read back after. A copy this client
does not understand raises UseTheBrowser with nothing changed.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://special-ops-content-backend.penpencil.co/workflows"
BUCKET = "special-ops-test-evaluation-prod"
RATING_TAGS = ("Introduction Competence", "Content Coverage", "Conclusion Competence",
               "Presentation & Structure", "Language Proficiency")
SUBMITTED, IN_EVALUATION = "Submitted", "InEvaluation"
AI_HYBRID = "AIHybridEvaluation"     # the page's other evaluation form, with its own ratings
TIMEOUT = (10, 60)
UPLOAD_TIMEOUT = (10, 300)
MAX_UPLOAD_BYTES = 150 * 1024 * 1024  # the page's own limit
EVALUATOR_PAGE = 50                   # the list has no search; the page reads all of it too
EVALUATOR_LIST_SECONDS = 30 * 60


class PortalApiError(RuntimeError):
    """The portal answered in a way this client does not understand."""


class PortalBusy(PortalApiError):
    """429 or a 5xx: nothing to fix, try again later."""
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class MarksNotSaved(PortalApiError):
    """The portal said yes and the copy shows otherwise: not a request to repeat."""


class UseTheBrowser(PortalApiError):
    """Nothing was changed, and the browser path can handle this copy or account."""


class PortalLoginRefused(UseTheBrowser):
    """The portal refused this ID's password. Not tried again, so it cannot lock the ID."""


class CopyMovedOn(Exception):
    """The copy left this evaluator after it was assigned here: another computer or evaluator
    took it or finished it. Nothing can be posted, and the browser could not post it either."""

    def __init__(self, submission, status, holder):
        super().__init__(f"Copy {submission} is {status} with {holder}")
        self.status, self.holder, self.finished = status, holder, status != IN_EVALUATION


class EvaluatorNotListed(ValueError):
    """Worded like the browser path's message, so the run stops the portal lanes the same way."""


_evaluators, _evaluators_at, _evaluators_lock = {}, 0.0, threading.Lock()


def portal_deadline(value):
    """The page's deadline: this computer's local time as UTC, to the second."""
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _retry_after(response):
    try:
        return float(response.headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None


class PortalApi:
    """One portal ID's session. One PortalDesk thread uses it at a time."""

    def __init__(self, account, password, http=None, clock=time.monotonic):
        self.account, self.password, self.http, self.clock = account, password, http or requests.Session(), clock
        self.token, self.user_id, self.tests = "", "", {}

    # ---------------------------------------------------------------- transport
    def sign_in(self):
        response = self.http.post(BASE + "/auth/login", json={"email": self.account, "password": self.password},
                                  headers={"Content-Type": "application/json"}, timeout=TIMEOUT)
        if response.status_code == 429 or response.status_code >= 500:
            raise PortalBusy(f"The portal is busy at sign-in (HTTP {response.status_code})", _retry_after(response))
        if not response.ok:
            raise PortalLoginRefused(f"The portal refused the sign-in of {self.account} (HTTP {response.status_code})")
        data = response.json().get("data") or {}
        if data.get("deleted") or not data.get("access_token") or not data.get("_id"):
            raise PortalLoginRefused(f"The portal gave no login for {self.account}")
        self.token, self.user_id = data["access_token"], data["_id"]

    def call(self, method, path, body=None):
        """The page's JSON request. A 401 or 403 signs in once more: the login may have ended."""
        if not self.token:
            self.sign_in()
        for attempt in (1, 2):
            response = self.http.request(method, BASE + path, json=body, timeout=TIMEOUT,
                                         headers={"Authorization": self.token, "Content-Type": "application/json"})
            if response.status_code not in (401, 403) or attempt == 2:
                break
            self.sign_in()
        if response.status_code == 429 or response.status_code >= 500:
            raise PortalBusy(f"The portal is busy (HTTP {response.status_code})", _retry_after(response))
        if not response.ok:
            raise PortalApiError(f"The portal refused {method} {path.split('?')[0]}: HTTP {response.status_code} {response.text[:200]}")
        return response.json()

    # -------------------------------------------------------------------- reads
    def details(self, test_id):
        if test_id not in self.tests:
            data = self.call("GET", f"/tev/v2/tests/{test_id}/details").get("data")
            if not data or data.get("_id") != test_id:
                raise PortalApiError(f"The portal gave no details for test {test_id}")
            self.tests[test_id] = data
        return self.tests[test_id]

    def copy_row(self, row):
        """This copy's row, found by the student and proved by every id the sheet gives."""
        found = self.call("GET", f"/tev/v2/tests/{row['test_id']}/students?page=1&limit=20&search={row['student_id']}").get("data") or []
        for entry in found:
            if (entry.get("_id") == row["submission_id"] and entry.get("testId") == row["test_id"]
                    and (entry.get("studentObj") or {}).get("_id") == row["student_id"]):
                return entry
        raise UseTheBrowser(f"Copy {row['submission_id']} of student {row['student_id']} was not found in test {row['test_id']}")

    def evaluator(self, email):
        """{"id", "name", "email"} of an evaluator the portal lists, read once for all IDs."""
        global _evaluators, _evaluators_at
        wanted = email.strip().lower()
        with _evaluators_lock:
            if wanted not in _evaluators or self.clock() - _evaluators_at > EVALUATOR_LIST_SECONDS:
                listed, page = {}, 1
                while True:
                    answer = self.call("GET", f"/tev/v2/users/allotment-map?role=Evaluator&isDummy=false&page={page}&limit={EVALUATOR_PAGE}")
                    entries = answer.get("data") or []
                    listed.update((str(entry.get("email") or "").strip().lower(), entry) for entry in entries)
                    if not entries or page * EVALUATOR_PAGE >= int((answer.get("paginate") or {}).get("totalCount") or 0):
                        break
                    page += 1
                _evaluators, _evaluators_at = listed, self.clock()
            entry = _evaluators.get(wanted)
            count = len(_evaluators)
        if entry is None:
            raise EvaluatorNotListed(f"Evaluator '{email}' was not found among {count} readable evaluator options for this test. "
                                     "Check the exact email in this test's dropdown; no copy was assigned.")
        return entry

    # ------------------------------------------------------------------- writes
    def notify(self, payload):
        """The page sends this after each step. A failure changes nothing on the copy."""
        try:
            self.call("POST", "/tev/v2/notifications", payload)
        except (PortalApiError, requests.RequestException) as exc:
            print(f"[direct] notification not sent: {exc}", flush=True)

    @staticmethod
    def _test_fields(details):
        category, program = details.get("categoryId") or {}, details.get("programId") or {}
        return {"testId": details.get("_id"), "testName": details.get("name"), "testSeriesId": category.get("_id"),
                "testSeriesName": category.get("name"), "programId": program.get("_id"), "programName": program.get("name")}

    def assign(self, row, options, before_submit):
        """Per copy, as Portal.assign_copy: assign, leave, reassign, or nothing to do."""
        found = self.copy_row(row)
        status = found.get("testActivityStatus")
        if status not in (SUBMITTED, IN_EVALUATION):
            return {"result": "finished", "status": status, "evaluator": found.get("checkedBy") or ""}
        evaluator = self.evaluator(options["evaluator_email"])
        if status == IN_EVALUATION and found.get("checkedBy") == evaluator.get("id"):
            return {"result": "already", "status": status, "evaluator": options["evaluator_email"]}
        details = self.details(row["test_id"])
        task = row["submission_id"]
        before_submit()
        self.call("PUT", f"/tev/tests/{row['test_id']}/students/{task}/assign",
                  {"assignId": evaluator["id"], "deadline": portal_deadline(options["deadline"]),
                   "totalQuestions": details.get("totalQuestions")})
        student = found.get("studentObj") or {}
        self.notify({**self._test_fields(details), "deadline": options["deadline"], "assignId": evaluator["id"],
                     "assignName": evaluator.get("name"), "assignEmail": evaluator.get("email"),
                     "studentCopyMap": [{"answerCopyId": task, "testActivityStatus": status, "studentId": task,
                                         "studentName": student.get("firstName")}],
                     "currentStatus": SUBMITTED, "submittedByRole": "Admin"})
        after = self.copy_row(row)
        if after.get("testActivityStatus") != IN_EVALUATION or after.get("checkedBy") != evaluator["id"]:
            raise PortalApiError(f"The portal did not take the assignment of {task}: it shows "
                                 f"{after.get('testActivityStatus')} with {after.get('checkedBy')}")
        return {"result": "assigned" if status == SUBMITTED else "reassigned", "status": status,
                "evaluator": found.get("checkedBy") or ""}

    def _upload(self, row, pdf):
        """The PDF into the portal's store, and the portal told of it. Returns its key."""
        data = Path(pdf).read_bytes()
        if len(data) > MAX_UPLOAD_BYTES:
            raise UseTheBrowser("The PDF is over the portal's 150 MB limit")
        test, task = row["test_id"], row["submission_id"]
        key = f"{test}/{task}/{uuid.uuid4()}.pdf"
        answer = self.call("GET", f"/tev/utils/ext/aws/s3/presigned-url?bucket={BUCKET}&key={key}&contentType=application/pdf")
        link = (answer.get("data") or {}).get("uploadURL")
        if not link:
            raise PortalApiError("The portal gave no upload link")
        stored = self.http.put(link, data=data, headers={"Content-Type": "application/pdf"}, timeout=UPLOAD_TIMEOUT)
        if stored.status_code == 429 or stored.status_code >= 500:
            raise PortalBusy(f"The file store is busy (HTTP {stored.status_code})", _retry_after(stored))
        if not stored.ok:
            raise PortalApiError(f"The file store refused the PDF: HTTP {stored.status_code}")
        # The page's own double slash ("/tev/" + "/tests/..."), as this route is known to work with it.
        self.call("PUT", f"/tev//tests/{test}/students/{task}/file", {"fileUrl": key})
        return key

    def submit(self, row, pdf, marks, total, comment, before_submit, uploaded=lambda key: None):
        """The checked PDF, each question's marks and the feedback, as the evaluator."""
        found = self.copy_row(row)
        if not self.user_id:
            self.sign_in()
        status, holder = found.get("testActivityStatus"), found.get("checkedBy")
        # 2 Nov 2026: three such copies went to the browser, whose login the direct sign-in had
        # ended, and waited for ever on "Unable to fetch task", holding every copy of the account.
        if status not in (SUBMITTED, IN_EVALUATION) or (status == IN_EVALUATION and holder != self.user_id):
            raise CopyMovedOn(row["submission_id"], status, holder)
        if status != IN_EVALUATION:
            raise UseTheBrowser(f"Copy {row['submission_id']} is {status} with {holder}, not waiting for {self.account}")
        details = self.details(row["test_id"])
        if found.get("type") == AI_HYBRID or details.get("isAnnotationEnabled"):
            raise UseTheBrowser("This test uses the portal's AI hybrid form")
        questions = int(details.get("totalQuestions") or 0)
        if any(question > questions or question < 1 for question in marks) and questions:
            raise UseTheBrowser(f"Marks for a question beyond the portal's {questions} questions")
        scores = [f"{float(marks.get(question, 0)):g}" for question in range(1, questions + 1)]
        if questions and abs(sum(float(score) for score in scores) - float(total)) > 1e-6:
            raise UseTheBrowser("The question marks do not add up to the total")
        if float(total) > float(details.get("totalMarks") or 0):
            raise UseTheBrowser(f"Total {total:g} is over the test's {details.get('totalMarks')} marks")
        key = row.get("portal_file") if row.get("portal_file") and row["portal_file"] == found.get("fileUrl") else ""
        if not key:
            key = self._upload(row, pdf)
            uploaded(key)
        body = {"checkedFile": key, "score": float(total), "questionsScore": scores, "comment": comment or "",
                "rating": [{"tag": tag, "rating": 0} for tag in RATING_TAGS], "totalQuestions": questions,
                "type": "test", "testName": details.get("name"), "evaluatorId": self.user_id,
                "subjectExpertise": [(details.get("subjectExpertise") or {}).get("_id")],
                "language": [details.get("language")], "testCategoryId": (details.get("categoryId") or {}).get("_id")}
        teachers = details.get("assignedTeachers") or []
        if teachers:
            body["teacherId"] = teachers[0]
        before_submit()
        task = row["submission_id"]
        self.call("PUT", f"/tev/tests/{row['test_id']}/tasks/evaluation/{task}", body)
        student = found.get("studentObj") or {}
        self.notify({**self._test_fields(details), "currentStatus": IN_EVALUATION, "submittedByRole": "Evaluator", "mappingId": task,
                     "studentCopyMap": [{"answerCopyId": task, "studentName": student.get("firstName"), "studentId": student.get("_id"),
                                         "testActivityStatus": found.get("testActivityStatus"), "deadline": found.get("deadline")}]})
        return self.read_back(row, key, float(total))

    def read_back(self, row, key, total):
        after = self.copy_row(row)
        saved = (after.get("evaluationObj") or [{}])[-1]
        if (after.get("testActivityStatus") == IN_EVALUATION or saved.get("checkedFile") != key
                or abs(float(saved.get("score") or 0) - total) > 1e-6):
            raise MarksNotSaved(f"The portal did not save the marks of {row['submission_id']}: it shows "
                                 f"{after.get('testActivityStatus')}, score {saved.get('score')}")
        return {"result": "done", "status": after.get("testActivityStatus")}

    # ------------------------------------------------------- after a lost answer
    def verify_assignment(self, row, options):
        found = self.copy_row(row)
        status = found.get("testActivityStatus")
        if status == SUBMITTED:
            return {"result": "not_done", "detail": "still Submitted"}
        if status != IN_EVALUATION:
            return {"result": "done", "detail": f"status {status}"}
        if found.get("checkedBy") == self.evaluator(options["evaluator_email"]).get("id"):
            return {"result": "done", "detail": "In Evaluation with this evaluator"}
        return {"result": "not_done", "detail": "In Evaluation with another evaluator"}

    def verify_evaluation(self, row):
        status = self.copy_row(row).get("testActivityStatus")
        if status == IN_EVALUATION:
            return {"result": "not_done", "detail": "still in the Pending list"}
        return {"result": "done", "detail": f"status {status}"}
