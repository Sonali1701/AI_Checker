"""Explicit cached-rerender orientation must preserve original source content."""
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from PIL import Image, ImageDraw, ImageChops, ImageStat
from tools import rerender


def fixture(existing_rotation=0):
    photograph=Image.new('RGB',(200,300),'white')
    draw=ImageDraw.Draw(photograph)
    draw.rectangle((10,10,60,50),fill='red')
    draw.rectangle((130,220,190,280),fill='blue')
    photo=io.BytesIO()
    photograph.save(photo,format='JPEG',quality=93)
    with fitz.open() as source:
        page=source.new_page(width=200,height=300)
        page.insert_image(page.rect,stream=photo.getvalue())
        page.insert_text((60,120),'ORIGINAL VECTOR',fontsize=9)
        page.set_rotation(existing_rotation)
        page=source.new_page(width=250,height=200)
        page.insert_text((40,80),'UNCHANGED PAGE',fontsize=12)
        return source.tobytes()


def raster(data, page):
    with fitz.open(stream=data,filetype='pdf') as source:
        return Image.open(io.BytesIO(source[page].get_pixmap(alpha=False).tobytes('png'))).convert('RGB')


def assert_same_visible_frame(test, actual, expected):
    test.assertEqual(actual.size,expected.size)
    # The vector text rasterizer may change an edge by one gray value when its
    # transform rotates. This is not source alteration; compressed photographs
    # and vector strings are separately required to remain exactly identical.
    difference=ImageChops.difference(actual,expected)
    test.assertLessEqual(max(hi for lo,hi in difference.getextrema()),2)
    test.assertLess(max(ImageStat.Stat(difference).mean),0.02)


class CachedRerenderOrientationTests(unittest.TestCase):
    def test_no_turn_returns_exact_original_bytes(self):
        source=fixture()
        self.assertIs(rerender.apply_reviewed_rotations(source,{}),source)
        self.assertIs(rerender.apply_reviewed_rotations(source,{1:0,2:0}),source)

    def test_counterclockwise_turn_preserves_photograph_vectors_and_other_page(self):
        for existing in (0,90,270):
            with self.subTest(existing_rotation=existing):
                source=fixture(existing)
                expected=raster(source,0).rotate(90,expand=True)
                actual=rerender.apply_reviewed_rotations(source,{1:90})
                turned=raster(actual,0)
                assert_same_visible_frame(self,turned,expected)
                self.assertEqual(raster(actual,1).tobytes(),raster(source,1).tobytes())
                with fitz.open(stream=source,filetype='pdf') as before, fitz.open(stream=actual,filetype='pdf') as after:
                    self.assertEqual(len(after),len(before))
                    self.assertEqual(after[0].get_text(),before[0].get_text())
                    self.assertEqual(after[1].get_text(),before[1].get_text())
                    original_photo=before.extract_image(before[0].get_images()[0][0])['image']
                    turned_photo=after.extract_image(after[0].get_images()[0][0])['image']
                    self.assertEqual(turned_photo,original_photo)

    def test_explicit_turns_and_page_numbers_are_validated_before_rotation(self):
        source=fixture()
        for metadata in ([],{'0':90},{'3':90},{'01':90},{'1':45},{'1':True},{'1':90.0},{'1':'90'},{'page_rotations':[]}):
            with self.subTest(metadata=metadata):
                with self.assertRaises(ValueError):
                    rerender.reviewed_rotation_map(metadata,2,source)
        self.assertEqual(rerender.reviewed_rotation_map({'1':90,'2':270},2,source),{1:90,2:270})

    def test_optional_source_and_ocr_bindings_reject_stale_frames(self):
        source=fixture()
        ocr=b'[]'
        metadata={'page_rotations':{'1':90},'source_sha256':hashlib.sha256(source).hexdigest(),'ocr_sha256':hashlib.sha256(ocr).hexdigest()}
        self.assertEqual(rerender.reviewed_rotation_map(metadata,2,source,ocr),{1:90})
        with self.assertRaises(ValueError):
            rerender.reviewed_rotation_map(metadata,2,source+b'changed',ocr)
        with self.assertRaises(ValueError):
            rerender.reviewed_rotation_map(metadata,2,source,b'[1]')
        with self.assertRaises(ValueError):
            rerender.reviewed_rotation_map(metadata,2,source)

    def test_cli_cached_rerender_preserves_inputs_and_does_not_infer_orientation(self):
        for explicit in (False,True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory(
                    dir=Path(__file__).resolve().parents[1],prefix='.workflow-test-rerender-') as folder:
                run=Path(folder)
                source=fixture()
                (run/'student.pdf').write_bytes(source)
                ocr=b'[]'
                (run/'ocr.json').write_bytes(ocr)
                (run/'report.json').write_text(json.dumps({'total_score':0,'max_total':0,'overall_remarks':'','questions':[],'annotations':[]}),encoding='utf-8')
                metadata={'page_rotations':{'1':90},'source_sha256':hashlib.sha256(source).hexdigest(),'ocr_sha256':hashlib.sha256(ocr).hexdigest()}
                (run/'rotations.json').write_text(json.dumps(metadata),encoding='utf-8')
                arguments=['rerender.py','--dir',str(run),'--student',str(run/'student.pdf'),'--out',str(run/'out.pdf')]
                if explicit:
                    arguments += ['--page-rotations',str(run/'rotations.json')]
                seen=[]
                def render(**kwargs):
                    seen.append(kwargs['student_pdf_bytes'])
                    return b'cached native render'
                with patch.object(sys,'argv',arguments), patch.object(rerender,'pdf_or_image_to_pngs',return_value=[]), patch.object(rerender,'build_evaluated_pdf',side_effect=render), patch.object(rerender.mathpix,'ocr_all_pages',side_effect=AssertionError('No paid OCR is permitted')):
                    rerender.main()
                self.assertEqual(len(seen),1)
                if explicit:
                    assert_same_visible_frame(self,raster(seen[0],0),raster(source,0).rotate(90,expand=True))
                else:
                    self.assertEqual(seen[0],source)
                self.assertEqual((run/'student.pdf').read_bytes(),source)
                self.assertEqual((run/'ocr.json').read_bytes(),ocr)


if __name__=='__main__':
    unittest.main()
