import os
import io
import csv
import logging
from datetime import datetime
from typing import Tuple, List

from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Charts
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# PDF generation
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Conversation states ---
AMOUNT, TERM, RATE, MANUALPAY, NAME = range(5)

# --- Register font with Cyrillic support ---
pdfmetrics.registerFont(TTFont("DejaVuSans", "DejaVuSans.ttf"))

# --- Utility functions ---
def parse_amount(text: str) -> float:
    text = text.replace(" ", "").replace(",", ".")
    return float(text)

def parse_rate(text: str) -> float:
    text = text.replace(" ", "").replace(",", ".")
    return float(text)

def parse_term(text: str) -> int:
    text = text.strip()
    return int(text)

def annuity_payment(principal: float, annual_rate_percent: float, months: int) -> float:
    r = (annual_rate_percent / 100.0) / 12.0
    if months <= 0:
        raise ValueError("Срок в месяцах должен быть > 0")
    if r == 0:
        return round(principal / months, 2)
    pmt = principal * r / (1 - (1 + r) ** (-months))
    return round(pmt, 2)

def build_schedule(principal: float, annual_rate_percent: float, months: int) -> Tuple[List[dict], dict]:
    r = (annual_rate_percent / 100.0) / 12.0
    payment = annuity_payment(principal, annual_rate_percent, months)

    balance = principal
    rows = []
    total_interest = 0.0
    total_principal = 0.0

    for m in range(1, months + 1):
        interest = round(balance * r, 2)
        principal_part = round(payment - interest, 2)
        if m == months:
            principal_part = round(balance, 2)
            payment_m = round(principal_part + interest, 2)
        else:
            payment_m = payment
        balance = round(balance - principal_part, 2)

        total_interest += interest
        total_principal += principal_part

        rows.append(
            {
                "Месяц": m,
                "Платеж": payment_m,
                "Проценты": interest,
                "Тело": principal_part,
                "Остаток": max(balance, 0.0),
            }
        )

    summary = {
        "Сумма кредита": round(principal, 2),
        "Ставка, % годовых": annual_rate_percent,
        "Срок, мес": months,
        "Ежемесячный платеж": payment,
        "Итого процентов": round(total_interest, 2),
        "Итого выплат": round(total_interest + principal, 2),
    }
    return rows, summary

def schedule_csv_bytes(rows: List[dict], summary: dict) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';')
    writer.writerow(["Сумма кредита", summary["Сумма кредита"]])
    writer.writerow(["Ставка, % годовых", summary["Ставка, % годовых"]])
    writer.writerow(["Срок, мес", summary["Срок, мес"]])
    writer.writerow(["Ежемесячный платеж", summary["Ежемесячный платеж"]])
    writer.writerow(["Итого процентов", summary["Итого процентов"]])
    writer.writerow(["Итого выплат", summary["Итого выплат"]])
    writer.writerow([])
    writer.writerow(["Месяц", "Платеж", "Проценты", "Тело", "Остаток"])
    for row in rows:
        writer.writerow([row["Месяц"], row["Платеж"], row["Проценты"], row["Тело"], row["Остаток"]])
    return buf.getvalue().encode("utf-8-sig")

def schedule_chart_bytes(rows: List[dict]) -> bytes:
    months = [r["Месяц"] for r in rows]
    interest = [r["Проценты"] for r in rows]
    principal = [r["Тело"] for r in rows]
    balance = [r["Остаток"] for r in rows]

    fig = plt.figure(figsize=(9, 5))
    plt.plot(months, interest, label="Interés en el pago")
    plt.plot(months, principal, label="Principal en el pago")
    plt.plot(months, balance, label="Saldo de deuda")
    plt.xlabel("Mes")
    plt.ylabel("Monto")
    plt.title("Estructura de pagos y saldo de deuda")
    plt.grid(True, alpha=0.3)
    plt.legend()

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=160)
    plt.close(fig)
    buf.seek(0)
    return buf.read()

# --- PDF CONTRACT ---
COMPANY_NAME = "BANCO BRADESCO ARGENTINA S/A"

def make_contract_pdf_bytes(borrower_name: str, lender_name: str, rows: List[dict], summary: dict, manual_payment: float) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
        title="CONTRATO DE PRÉSTAMO",
        author=COMPANY_NAME,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="TitleBlue", parent=styles['Title'], alignment=1, fontName="DejaVuSans", textColor=colors.blue))
    styles.add(ParagraphStyle(name="Justify", parent=styles['Normal'], leading=14, fontName="DejaVuSans"))
    styles.add(ParagraphStyle(name="NormalSmall", fontName="DejaVuSans", fontSize=10, leading=12))

    story = []

    # Title
    story.append(Paragraph("CONTRATO DE PRÉSTAMO", styles["TitleBlue"]))
    story.append(Spacer(1, 12))

    # Parties
    today = datetime.now().strftime("%Y-%m-%d")
    party_text = (
        f"Entre: <b>{borrower_name}</b> (el \"Prestatario\")<br/>"
        f"y <b>{lender_name}</b> (el \"Prestamista\")."
    )
    story.append(Paragraph(party_text, styles["Justify"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Fecha de firma: <b>{today}</b>", styles["Justify"]))
    story.append(Spacer(1, 12))

    # Loan conditions
    cond = [
        ["Condiciones del préstamo", ""],
        ["Importe", f"{summary['Сумма кредита']:.2f} ARS"],
        ["Plazo (meses)", f"{summary['Срок, мес']}"],
        ["Tipo de interés (TAE)", f"{summary['Ставка, % годовых']}%"],
        ["Pago manual", f"{manual_payment:.2f} ARS"],
    ]
    cond_table = Table(cond, colWidths=[180, 330])
    cond_table.setStyle(TableStyle([
        ('SPAN', (0,0), (-1,0)),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('TEXTCOLOR', (0,0), (-1,0), colors.black),
        ('FONTNAME', (0,0), (-1,-1), 'DejaVuSans'),
        ('FONTSIZE', (0,0), (-1,0), 11),
        ('BOX', (0,0), (-1,-1), 0.5, colors.grey),
        ('INNERGRID', (0,0), (-1,-1), 0.25, colors.grey),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(cond_table)
    story.append(Spacer(1, 12))

    # Payment warning
    story.append(Paragraph("Debe pagar la cuota dentro de 24 horas, de lo contrario el préstamo puede ser cancelado y su calificación crediticia bajará.", styles["Justify"]))
    story.append(Spacer(1, 12))

    # Bank info
    story.append(Paragraph("Este crédito es otorgado por BANCO BRADESCO ARGENTINA S/A, que trabaja con nosotros 24/7 en condiciones individuales, con 13 años de experiencia en el sector.", styles["NormalSmall"]))
    story.append(Spacer(1, 20))

    # Amortization table
    story.append(Paragraph("Calendario de pagos (Amortización)", styles['NormalSmall']))
    data = [["Mes", "Pago", "Interés", "Principal", "Saldo"]]
    for r in rows:
        data.append([
            r["Месяц"],
            f"{r['Платеж']:.2f}",
            f"{r['Проценты']:.2f}",
            f"{r['Тело']:.2f}",
            f"{r['Остаток']:.2f}",
        ])
    table = Table(data, repeatRows=1, colWidths=[50, 90, 90, 90, 90])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('FONTNAME', (0,0), (-1,-1), 'DejaVuSans'),
        ('ALIGN', (0,0), (-1,0), 'CENTER'),
        ('ALIGN', (0,1), (-1,-1), 'RIGHT'),
        ('BOX', (0,0), (-1,-1), 0.5, colors.grey),
        ('INNERGRID', (0,0), (-1,-1), 0.25, colors.grey),
    ]))
    story.append(table)

    doc.build(story)
    buf.seek(0)
    return buf.read()

# --- Bot handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Привет! Я помогу посчитать кредит (аннуитет) и выдам график платежей.\n\n"
        "Команды:\n"
        "• /calc — рассчитать график и сформировать договор (PDF, испанский)\n"
        "• /help — помощь\n\n"
        "Поддерживается любая валюта — считаю числа."
    )
    await update.message.reply_text(text)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Используйте /calc и следуйте подсказкам: сумма → срок в месяцах → ставка годовая → платеж вручную → ФИО."
    )

async def calc_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Введите сумму кредита (число):")
    return AMOUNT

async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = parse_amount(update.message.text)
        if amount <= 0:
            raise ValueError("Сумма должна быть больше нуля.")
        context.user_data["amount"] = amount
        await update.message.reply_text("Введите срок кредита в месяцах (целое число):")
        return TERM
    except Exception:
        await update.message.reply_text("Ошибка: введите корректную сумму (например, 100000 или 100000.50)")
        return AMOUNT

async def term_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        term = parse_term(update.message.text)
        if term <= 0:
            raise ValueError("Срок должен быть больше нуля.")
        context.user_data["term"] = term
        await update.message.reply_text("Введите годовую процентную ставку (например, 12.5):")
        return RATE
    except Exception:
        await update.message.reply_text("Ошибка: введите корректный срок в месяцах (например, 12)")
        return TERM

async def rate_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        rate = parse_rate(update.message.text)
        if rate < 0:
            raise ValueError("Ставка не может быть отрицательной.")
        context.user_data["rate"] = rate
        await update.message.reply_text("Введите желаемую сумму платежа (manual):")
        return MANUALPAY
    except Exception:
        await update.message.reply_text("Ошибка: введите корректную ставку (например, 12.5)")
        return RATE

async def manual_payment_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        manual_payment = parse_amount(update.message.text)
        if manual_payment <= 0:
            raise ValueError("Сумма должна быть больше нуля.")
        context.user_data["manual_payment"] = manual_payment
        await update.message.reply_text("Введите вашу Фамилию и Имя (для договора):")
        return NAME
    except Exception:
        await update.message.reply_text("Ошибка: введите корректную сумму (например, 10000)")
        return MANUALPAY

async def name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Введите Фамилию и Имя не пустое:")
        return NAME
    context.user_data["name"] = name

    amount = context.user_data["amount"]
    term = context.user_data["term"]
    rate = context.user_data["rate"]
    manual_payment = context.user_data["manual_payment"]

    rows, summary = build_schedule(amount, rate, term)

    # Отправляем сводку
    summary_text = (
        f"Рассчитано для:\n"
        f"Сумма: {summary['Сумма кредита']:.2f} ARS\n"
        f"Срок: {summary['Срок, мес']} мес.\n"
        f"Ставка: {summary['Ставка, % годовых']}%\n"
        f"Ваш платеж (manual): {manual_payment:.2f} ARS"
    )
    await update.message.reply_text(summary_text)

    # Отправляем график
    chart_bytes = schedule_chart_bytes(rows)
    await update.message.reply_photo(photo=chart_bytes)

    # Отправляем CSV
    csv_bytes = schedule_csv_bytes(rows, summary)
    await update.message.reply_document(document=InputFile(io.BytesIO(csv_bytes), filename="schedule.csv"))

    # Отправляем PDF договор
    pdf_bytes = make_contract_pdf_bytes(name, COMPANY_NAME, rows, summary, manual_payment)
    await update.message.reply_document(document=InputFile(io.BytesIO(pdf_bytes), filename="contrato_prestamo.pdf"))

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отмена расчета. Для начала введите /calc")
    return ConversationHandler.END

def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("Не найден токен в переменных окружения TELEGRAM_BOT_TOKEN")
        return

    application = ApplicationBuilder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("calc", calc_start)],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & (~filters.COMMAND), amount_received)],
            TERM: [MessageHandler(filters.TEXT & (~filters.COMMAND), term_received)],
            RATE: [MessageHandler(filters.TEXT & (~filters.COMMAND), rate_received)],
            MANUALPAY: [MessageHandler(filters.TEXT & (~filters.COMMAND), manual_payment_received)],
            NAME: [MessageHandler(filters.TEXT & (~filters.COMMAND), name_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(conv_handler)

    application.run_polling()

if __name__ == "__main__":
    main()
