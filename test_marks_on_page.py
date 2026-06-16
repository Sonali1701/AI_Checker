"""Test PDF rendering with marks to verify circle centering works in actual use."""
import fitz
from pdf_renderer import _draw_number_in_circle, _draw_tick

# Create a test PDF mimicking an answer sheet
doc = fitz.open()
page = doc.new_page(width=595, height=842)

# Add title
page.insert_text((50, 30), "TEST: Circle Centering Verification", fontsize=14, fontname="hebo")

# Create mock answer lines with marks
y_start = 100
line_height = 50

# Scenario 1: Multi-step question with multiple marks
page.insert_text((50, y_start), "Q1: Multi-step question", fontsize=11)
for step in range(1, 4):
    y = y_start + step * line_height
    page.draw_line(fitz.Point(50, y), fitz.Point(500, y), width=0.5)
    page.insert_text((50, y + 5), f"Step {step} answer text here...", fontsize=10)

    # Place tick and circle mark (like grader would)
    tick_x = 450
    _draw_tick(page, tick_x, y + 15, size=8)

    # Circle with step number (multi-step uses about 75px spacing)
    mark_x = tick_x + 75
    _draw_number_in_circle(page, mark_x, y + 15, str(step), size=11)

# Scenario 2: Single mark questions
y_start2 = y_start + 250
page.insert_text((50, y_start2), "Q2-5: Single mark questions", fontsize=11)

for qnum in range(2, 6):
    y = y_start2 + (qnum - 1) * line_height
    page.draw_line(fitz.Point(50, y), fitz.Point(500, y), width=0.5)
    page.insert_text((50, y + 5), f"Q{qnum} answer...", fontsize=10)

    # Single mark with circle
    tick_x = 450
    _draw_tick(page, tick_x, y + 15, size=8)
    mark_x = tick_x + 75  # Using consistent spacing for test
    _draw_number_in_circle(page, mark_x, y + 15, "1", size=11)

# Save
doc.save("test_marks_on_page.pdf")
print("Test PDF created: test_marks_on_page.pdf")
print("Check: All numbers should be CENTERED in circles, not at top")
