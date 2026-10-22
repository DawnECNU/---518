from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "阶段一代码" / "docs" / "阶段一项目报告.pdf"
REPOSITORY = "https://github.com/DawnECNU/---518"

pdfmetrics.registerFont(TTFont("CN", r"C:\Windows\Fonts\Deng.ttf"))

reader = PdfReader(str(REPORT))
writer = PdfWriter()
writer.clone_document_from_reader(reader)

page = writer.pages[0]
width, height = A4
overlay_buffer = BytesIO()
overlay = canvas.Canvas(overlay_buffer, pagesize=A4)
overlay.setFont("CN", 10.5)
overlay.setFillColor(HexColor("#333333"))
overlay.drawCentredString(
    width / 2,
    height - 332,
    f"GitHub 仓库：{REPOSITORY}",
)
overlay.save()
overlay_buffer.seek(0)

overlay_page = PdfReader(overlay_buffer).pages[0]
page.merge_page(overlay_page)

with REPORT.open("wb") as stream:
    writer.write(stream)

print(REPORT)
