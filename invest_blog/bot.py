from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Settings
from .posts import PostGenerator
from .storage import DraftStore
from .tinvest import TInvest

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

RUBRICS = ("weekly", "monthly", "plan", "paper", "glossary", "purchase")
HELP = ("🤖 Invest Blog Assistant — только черновики, без публикаций\n\n"
        "/weekly — отчёт за последнюю полную пн–пт\n"
        "/monthly — за прошлый календарный месяц\n"
        "/plan — календарь на текущую неделю\n"
        "/paper — бумага недели\n"
        "/glossary — ликбез\n"
        "/purchase — последняя покупка за 60 дней\n"
        "/drafts — последние черновики\n"
        "/show ID — показать текст черновика\n"
        "/edit ID НОВЫЙ_ТЕКСТ — заменить текст (можно несколькими строками)\n"
        "/done ID — отметить вручную опубликованным\n"
        "/whoami — показать ID чата\n\n"
        "Чтобы исправить черновик, можно также ответить на сообщение бота новым текстом."
       )


class InvestBot:
    def __init__(self, settings: Settings):
        settings.validate_bot()
        self.settings = settings
        self.store = DraftStore(settings.db_path)
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)

    def _generate_sync(self, kind: str):
        # Never reuse requests.Session across multiple threads.
        api = TInvest(self.settings.invest_token, self.settings.timezone)
        gen = PostGenerator(api, self.settings.account_ids, self.settings.cbr_path)
        return gen.generate(kind, datetime.now(self.settings.timezone).date())

    def allowed(self, update: Update) -> bool:
        return bool(update.effective_chat and update.effective_chat.id == self.settings.telegram_chat_id
                    and update.effective_chat.type == "private")

    async def guard(self, update: Update) -> bool:
        if self.allowed(update):
            return True
        if update.effective_message:
            await update.effective_message.reply_text("Доступ ограничен.")
        return False

    async def send_draft(self, application: Application, draft_id: int) -> None:
        row = self.store.get(draft_id)
        if row is None:
            return
        label = f"📝 Черновик #{draft_id} — {row['kind']} ({row['period']})\n\n"
        body = row["body"]
        if len(label + body) <= 3900:
            msg = await application.bot.send_message(self.settings.telegram_chat_id, label + body)
        else:
            payload = io.BytesIO(body.encode("utf-8"))
            payload.name = f"draft_{draft_id}.txt"
            msg = await application.bot.send_document(
                self.settings.telegram_chat_id, payload,
                caption=label + "Текст длиннее лимита Telegram — он приложен файлом.")
        self.store.link_message(msg.message_id, draft_id)
        self.store.sent(draft_id)

    async def run_scheduled(self, kind: str, application: Application) -> None:
        try:
            draft = await asyncio.to_thread(self._generate_sync, kind)
            draft_id, fresh = self.store.insert(draft, scheduled=True)
            if fresh:
                await self.send_draft(application, draft_id)
        except Exception as exc:
            log.exception("Не удалось сформировать %s", kind)
            try:
                await application.bot.send_message(
                    self.settings.telegram_chat_id,
                    f"⚠️ Ошибка подготовки рубрики {kind}: {type(exc).__name__}. Проверь логи и вызови /{kind} позже.")
            except Exception:
                log.exception("Не получилось отправить уведомление об ошибке")

    async def startup(self, app: Application) -> None:
        # Retry unsent drafts (possible duplicate delivery if process crashed between Telegram send and db commit).
        for row in self.store.unsent():
            try:
                await self.send_draft(app, row["id"])
            except Exception:
                log.exception("Не удалось дослать черновик #%s", row["id"])
        if not self.settings.startup_catchup:
            return
        now = datetime.now(self.settings.timezone)
        weekdays = {0: "plan", 2: "paper", 4: "glossary", 5: "weekly"}
        due = [weekdays[now.weekday()]] if now.weekday() in weekdays else []
        if now.day == 1:
            due.append("monthly")
        for kind in due:
            if (now.hour, now.minute) >= self.settings.times[kind]:
                await self.run_scheduled(kind, app)

    async def post_init(self, application: Application) -> None:
        weekdays = {"plan": "mon", "paper": "wed", "glossary": "fri", "weekly": "sat"}
        for kind, weekday in weekdays.items():
            h, m = self.settings.times[kind]
            self.scheduler.add_job(self.run_scheduled,
                                   CronTrigger(day_of_week=weekday, hour=h, minute=m, timezone=self.settings.timezone),
                                   args=[kind, application], id=kind, max_instances=1,
                                   misfire_grace_time=3600, coalesce=True)
        h, m = self.settings.times["monthly"]
        self.scheduler.add_job(self.run_scheduled,
                               CronTrigger(day=1, hour=h, minute=m, timezone=self.settings.timezone),
                               args=["monthly", application], id="monthly", max_instances=1,
                               misfire_grace_time=3600, coalesce=True)
        self.scheduler.start()
        # Don't block Telegram long polling while contacting the broker.
        asyncio.create_task(self.startup(application))
        log.info("Бот запущен; расписание в %s", self.settings.timezone)

    async def post_shutdown(self, application: Application) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def whoami(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(f"ID этого чата: {update.effective_chat.id}")

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self.guard(update):
            await update.effective_message.reply_text(HELP)

    async def generate_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.guard(update):
            return
        kind = update.effective_message.text.split()[0].split("@")[0].lstrip("/")
        if kind not in RUBRICS:
            return
        try:
            draft = await asyncio.to_thread(self._generate_sync, kind)
            draft_id, _ = self.store.insert(draft, scheduled=False)
            await self.send_draft(context.application, draft_id)
        except Exception as exc:
            log.exception("Ошибка при ручной генерации %s", kind)
            await update.effective_message.reply_text(
                f"⚠️ Не удалось создать черновик: {type(exc).__name__}: {str(exc)[:200]}")

    async def drafts(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.guard(update):
            return
        rows = self.store.list_recent()
        lines = [f"#{row['id']} — {row['kind']} ({row['period']}) [{row['state']}]" for row in rows]
        await update.effective_message.reply_text("\n".join(lines) or "Черновиков пока нет.")

    async def show(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.guard(update):
            return
        try:
            draft_id = int(context.args[0])
            if not self.store.get(draft_id):
                raise ValueError("нет такого черновика")
            await self.send_draft(context.application, draft_id)
        except (IndexError, ValueError):
            await update.effective_message.reply_text("Используй /show 12")

    async def done(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.guard(update):
            return
        try:
            draft_id = int(context.args[0])
            if self.store.get(draft_id) is None:
                raise ValueError()
            self.store.mark_done(draft_id)
            await update.effective_message.reply_text(f"Черновик #{draft_id} отмечен как опубликованный (вручную).")
        except (IndexError, ValueError):
            await update.effective_message.reply_text("Используй /done 12")

    async def edit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self.guard(update):
            return
        raw = update.effective_message.text
        parts = raw.split(maxsplit=2)
        if len(parts) < 3 or not parts[1].isdigit() or not parts[2].strip():
            await update.effective_message.reply_text("Используй /edit 12 Новый текст поста")
            return
        draft_id = int(parts[1])
        if self.store.get(draft_id) is None:
            await update.effective_message.reply_text("Черновик не найден")
            return
        self.store.set_body(draft_id, parts[2].strip())
        await update.effective_message.reply_text(f"✅ Текст черновика #{draft_id} заменён. /show {draft_id}")

    async def reply_edit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed(update):
            return
        message = update.effective_message
        if not message or not message.reply_to_message or not message.text:
            return
        draft_id = self.store.draft_for_message(message.reply_to_message.message_id)
        if draft_id is None:
            return
        if not message.text.strip():
            return
        self.store.set_body(draft_id, message.text.strip())
        await message.reply_text(f"✅ Черновик #{draft_id} обновлён. Посмотреть: /show {draft_id}")

    def run(self) -> None:
        app = (ApplicationBuilder().token(self.settings.telegram_token)
               .post_init(self.post_init).post_shutdown(self.post_shutdown).build())
        app.add_handler(CommandHandler(("start", "help"), self.help))
        app.add_handler(CommandHandler("whoami", self.whoami))
        for kind in RUBRICS:
            app.add_handler(CommandHandler(kind, self.generate_command))
        app.add_handler(CommandHandler("drafts", self.drafts))
        app.add_handler(CommandHandler("show", self.show))
        app.add_handler(CommandHandler("edit", self.edit))
        app.add_handler(CommandHandler("done", self.done))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.reply_edit))
        app.run_polling(drop_pending_updates=False)
