"""Write the PW Skills Checker how-to guide as a PDF.

usage: make_skills_guide.py OUT_PDF"""
import io
import sys

import fitz

out = sys.argv[1]

CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.45; color: #1d1b3a; }
h1 { font-size: 20pt; color: #312e81; margin: 0 0 2pt 0; }
h2 { font-size: 13pt; color: #3730a3; margin: 14pt 0 4pt 0; }
p { margin: 3pt 0; }
ol, ul { margin: 2pt 0 4pt 0; }
li { margin: 2pt 0; }
.lead { font-size: 11.5pt; color: #3f3d63; margin-bottom: 8pt; }
.box { background-color: #fff4e5; border: 1pt solid #f3c27a; padding: 6pt 8pt; margin: 6pt 0; }
.ok { background-color: #eef7f0; border: 1pt solid #9fd3ad; padding: 6pt 8pt; margin: 6pt 0; }
b { color: #1d1b3a; }
code { font-family: monospace; font-size: 9.5pt; background-color: #f1f1f8; }
table { border-collapse: collapse; margin: 4pt 0; }
td { border: 0.5pt solid #c9c9dc; padding: 3pt 6pt; vertical-align: top; }
"""

HTML = """
<h1>PW Skills Checker</h1>
<p class="lead">Checks learners' PW Skills assignment submissions with AI and posts the marks and
feedback back to the admin portal, the same fields a mentor fills in by hand.</p>

<h2>What you need</h2>
<ul>
<li>A Windows computer with <b>Google Chrome</b> or <b>Microsoft Edge</b>.</li>
<li>The <b>PW VPN</b> switched on, or the PW office network. The admin portal's data service is
only reachable from inside PW's network.</li>
<li>Your <b>@pw.live Google account</b>, added to this tool's access list. If the tool says you
are not allowed, send your email to the person who gave you the tool.</li>
<li>An <b>admin account</b> for admin-v2.penpencil.co that can open PW Skills assignments.</li>
</ul>

<h2>Starting it</h2>
<ol>
<li>Put <code>PWSkillsChecker.exe</code> in a folder of its own, for example
<code>Documents\\PW Skills Checker</code>. It saves its runs next to itself, in a folder
named <code>pw_skills_data</code>.</li>
<li>Double-click it. The first start takes up to a minute. If Windows shows "Windows protected
your PC", click <b>More info</b> and then <b>Run anyway</b>.</li>
<li>A black window opens. <b>Keep it open</b> while you work: closing it closes the tool.
Your browser opens the tool's page by itself.</li>
<li>Sign in with your @pw.live Google account. You stay signed in for about a week.</li>
</ol>

<h2>1. Sign in to the admin portal</h2>
<p>Click <b>Sign in to the admin portal</b>. A separate browser window opens at
admin-v2.penpencil.co. Sign in there with the admin account. The window closes by itself and
the page shows a green dot: <b>Signed in to the admin portal</b>.</p>

<h2>2. Choose what to check</h2>
<ol>
<li><b>Batch:</b> type the batch code, for example <code>Pro_ON_MSF_Sept26</code>. When one batch
matches exactly, it is chosen for you. You can also paste "Course name | Batch code" from a
sheet. Exact matches are listed first with a green <b>Exact match</b> tag.</li>
<li><b>Assignments:</b> leave every box unticked to take all assignments of the batch, or tick the
ones you want. The filter box helps with long lists.</li>
<li><b>Learners</b> (optional): phone numbers or emails with commas between, to check only those
learners. Leave it empty for everyone.</li>
<li><b>More options:</b> how many copies to take in this run (oldest first) and how many to
check at the same time.</li>
<li>Read the summary line, then click <b>Fetch and check</b>.</li>
</ol>
<div class="box"><b>Post marks by itself</b> sends marks to learners with no one reading them first.
Evaluate is final: the learner cannot submit again. Leave it off until you trust the marks.</div>

<!--new page--><h2>3. Check the marks</h2>
<p>Each copy takes about 2 to 5 minutes. Click a run in <b>Runs</b> to see its copies. The tiles
at the top filter the list:</p>
<table>
<tr><td><b>Waiting for you</b></td><td>Checked. Read the marks and feedback, then post.</td></tr>
<tr><td><b>Needs a person</b></td><td>The tool will not post it. The reason is shown under the copy:
for example 0 marks (the portal does not accept 0), the answer belongs to another test, or the
file could not be read. Mark these on the admin portal yourself.</td></tr>
<tr><td><b>In progress</b></td><td>Waiting or being checked. A copy still checking after 15 minutes
is shown in amber.</td></tr>
<tr><td><b>Posted</b></td><td>On the portal. "Evaluated by someone else" means a mentor got there
first, and the tool left it alone.</td></tr>
</table>
<ul>
<li><b>Checked PDF</b> shows the learner's work with the marks on it. It stays on your computer;
the portal only receives marks and feedback.</li>
<li><b>Read / change</b> lets you change the marks or the feedback. The portal takes at most 500
characters of feedback; the counter shows how many you have used.</li>
<li><b>Check again</b> runs the AI check once more. It is paid for again, so use it only when a
mark looks wrong.</li>
</ul>

<h2>4. Post to the portal</h2>
<p>Click <b>Tick all checked copies</b> (or tick copies one by one), then <b>Post ticked marks</b>.
Pass or fail is set by the portal at 60%. Before posting, the tool reads each submission again:
a copy evaluated by someone else meanwhile, or replaced by the learner, is not posted.</p>

<h2>5. The run report</h2>
<p>When a run stops, the bottom of its page shows a <b>Run report</b>: how many copies were posted,
are waiting for you or need a person, and the reasons copies were not posted, with a count for
each. Click <b>Download report (Excel)</b> for the full list: every copy with its marks, status and
reason, every link in each learner's submission with what the tool did with it (opened and read,
or not opened and why), and the files it read. A copy of the report is also saved next to the run,
in the <code>pw_skills_data</code> folder.</p>
<p>A learner whose PDF only says "Click to open the Excel file" is not given 0: the tool opens the
link when it can, and when it cannot (for example, the PDF was printed instead of saved, which
drops links) the copy goes to <b>Needs a person</b> with the reason.</p>

<!--new page--><h2>Stopping and coming back</h2>
<p><b>Stop</b> finishes the copies in hand and stops. <b>Resume / fetch new copies</b> carries on
and picks up new submissions. To quit, close the black window. Runs are saved: open the tool
again and click <b>Open</b> on the run.</p>

<h2>If something goes wrong</h2>
<table>
<tr><td>The admin portal cannot be reached</td><td>Switch on the PW VPN and try again.</td></tr>
<tr><td>"Admin sign-in needed"</td><td>The admin sign-in expired. Click <b>Sign in to the admin
portal</b> again, then <b>Resume</b>.</td></tr>
<tr><td>You are not allowed to use the tool</td><td>Your @pw.live email is not on the access list
yet. Send it to the person who gave you the tool.</td></tr>
<tr><td>"An older AI Checker is still running"</td><td>Another copy of the tool is open. Close its
black window, then start this one again.</td></tr>
<tr><td>Anything else</td><td>Send the file <code>aichecker.log</code> from the tool's folder to the
person who gave you the tool.</td></tr>
</table>
<div class="ok">The tool only ever writes marks and feedback, through the same Evaluate action a
mentor uses. It never posts a copy twice, and it never posts while you are reading it.</div>
"""

buf = io.BytesIO()
writer = fitz.DocumentWriter(buf)
page = fitz.paper_rect("a4")
where = page + (48, 48, -48, -48)
for part in HTML.split("<!--new page-->"):
    story = fitz.Story(html=part, user_css=CSS)
    more = True
    while more:
        more, filled = story.place(where)
        assert fitz.Rect(filled).y1 <= where.y1 + 1, "guide content ran off the page"
        device = writer.begin_page(page)
        story.draw(device)
        writer.end_page()
writer.close()
open(out, "wb").write(buf.getvalue())
document = fitz.open(out)
print(out, document.page_count, "pages")
