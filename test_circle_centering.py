import fitz
from pdf_renderer import _draw_number_in_circle

# Create a test PDF
doc = fitz.open()
page = doc.new_page(width=400, height=300)

# Draw several test circles with different number sizes
test_cases = [
    (100, 80, "1", 11),
    (200, 80, "2", 11),
    (300, 80, "12", 11),
    (100, 180, "0.5", 13),
    (200, 180, "3", 13),
    (300, 180, "99", 13),
]

for x, y, text, size in test_cases:
    # Draw reference point and crosshair to see where numbers should be centered
    page.draw_circle(fitz.Point(x, y), 2, color=(0, 0, 1), width=1)  # Blue dot at center
    page.draw_line(fitz.Point(x-20, y), fitz.Point(x+20, y), color=(0.8, 0.8, 0.8), width=0.5)  # Horizontal line
    page.draw_line(fitz.Point(x, y-20), fitz.Point(x, y+20), color=(0.8, 0.8, 0.8), width=0.5)  # Vertical line

    # Draw the number in circle
    _draw_number_in_circle(page, x, y, text, size=size)

# Save test PDF
doc.save("test_circle_centering.pdf")
print("Test PDF created: test_circle_centering.pdf")
print("Check if numbers are centered in circles (not at top)")
