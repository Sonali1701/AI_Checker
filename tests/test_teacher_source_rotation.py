"""The native layout must compose the displayed source frame, including crops."""
import hashlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageChops, ImageStat
import pdf_renderer
from grader import GradeReport


def source_booklet(turn,cropped):
    photograph=Image.new('RGB',(360,440),'white')
    pen=ImageDraw.Draw(photograph)
    pen.rectangle((40,50,100,110),fill='red')
    pen.rectangle((260,320,310,370),fill='blue')
    data=io.BytesIO()
    photograph.save(data,format='JPEG',quality=93)
    with fitz.open() as document:
        page=document.new_page(width=360,height=440)
        page.insert_image(page.rect,stream=data.getvalue())
        page.insert_text((110,185),'ORIGINAL VECTOR WORDS',fontsize=10)
        if cropped:
            page.set_cropbox(fitz.Rect(25,35,335,415))
        page.set_rotation(turn)
        page=document.new_page(width=250,height=300)
        page.insert_text((40,120),'UNROTATED SECOND PAGE',fontsize=12)
        return document.tobytes()


def image(page,clip=None):
    return Image.open(io.BytesIO(page.get_pixmap(
        matrix=fitz.Matrix(pdf_renderer._INK_DPI/72,pdf_renderer._INK_DPI/72),
        alpha=False,clip=clip).tobytes('png'))).convert('RGB')


class TeacherSourceRotationTests(unittest.TestCase):
    def test_native_composition_matches_every_rotated_or_cropped_source_page(self):
        for turn in (0,90,180,270):
            for cropped in (False,True):
                with self.subTest(turn=turn,cropped=cropped):
                    source=source_booklet(turn,cropped)
                    original_hash=hashlib.sha256(source).hexdigest()
                    report=GradeReport(total_score=0,max_total=0,overall_remarks='',questions=[],annotations=[])
                    with patch.object(pdf_renderer,'TEACHER_LAYOUT',True):
                        output=pdf_renderer.build_evaluated_pdf(source,'original.pdf',[],report,[])
                    self.assertEqual(hashlib.sha256(source).hexdigest(),original_hash)
                    with fitz.open(stream=source,filetype='pdf') as raw, fitz.open(stream=output,filetype='pdf') as evaluated:
                        self.assertEqual(len(evaluated),len(raw))
                        for index,page in enumerate(evaluated):
                            bounds=fitz.Rect(report.render_page_layouts[str(index+1)]['content_bbox'])
                            actual=image(page,bounds)
                            expected=image(raw[index])
                            self.assertEqual(actual.size,expected.size)
                            # The red divider/header touch the external page edge.
                            # Compare the actual source frame inside those rules.
                            interior=(3,3,actual.width-3,actual.height-3)
                            actual,expected=actual.crop(interior),expected.crop(interior)
                            difference=ImageChops.difference(actual,expected)
                            # Crop/quarter-turn XForms can change vector glyph
                            # antialiasing slightly. A wrong frame produces a
                            # large difference; colored photo landmarks must
                            # still agree within one raster pixel.
                            self.assertLess(max(ImageStat.Stat(difference).mean),0.5)
                            for channel in ((0,2) if index == 0 else ()):
                                centers=[]
                                for picture in (actual,expected):
                                    values=np.asarray(picture).astype(float)
                                    other=[n for n in (0,1,2) if n!=channel]
                                    ys,xs=np.where((values[:,:,channel]>180)
                                        &(values[:,:,channel]>2*values[:,:,other[0]])
                                        &(values[:,:,channel]>2*values[:,:,other[1]]))
                                    self.assertGreater(len(xs),100)
                                    centers.append((xs.mean(),ys.mean()))
                                self.assertLess(max(abs(a-b) for a,b in zip(*centers)),1.0)
                            for word in raw[index].get_text('words'):
                                expected_box=fitz.Rect(word[:4])*raw[index].rotation_matrix
                                expected_box += (bounds.x0,bounds.y0,bounds.x0,bounds.y0)
                                matches=[w for w in page.get_text('words') if w[4]==word[4]]
                                self.assertTrue(matches)
                                actual_box=fitz.Rect(matches[0][:4])
                                self.assertLess(max(abs(a-b) for a,b in zip(actual_box,expected_box)),0.02)
                        self.assertIn('ORIGINAL VECTOR WORDS',evaluated[0].get_text())
                        self.assertIn('UNROTATED SECOND PAGE',evaluated[1].get_text())
                        old_image=raw.extract_image(raw[0].get_images()[0][0])['image']
                        new_image=evaluated.extract_image(evaluated[0].get_images()[0][0])['image']
                        self.assertEqual(new_image,old_image)


if __name__=='__main__':
    unittest.main()
