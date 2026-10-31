"""Private Telegram editorial bot. Channel publishing requires explicit approval."""
from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import (Application, ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, ContextTypes, MessageHandler, filters)

from .config import Settings
from .portfolio_store import PortfolioStore
from .editorial_workflow import EditorialWorkflow
from .posts import PostGenerator
from .editorial import ContentPlanner
from .publication_schedule import WEEKLY_RULES
from .storage import DraftStore
from .sync import PortfolioSync
from .tinvest import TInvest
from .placements import PlacementStore, PlacementMonitor
from .tech_news import TechNewsStore, TechNewsCollector

log = logging.getLogger(__name__)
# Avoid the token embedded in Telegram request URLs in INFO logs.
logging.getLogger('httpx').setLevel(logging.WARNING)
RUBRICS = ('weekly', 'monthly', 'plan', 'paper', 'glossary', 'purchase')
HELP = ("🤖 Invest Blog Assistant\n\n"
        "/weekly, /monthly, /plan, /paper, /glossary, /purchase — черновики\n"
        "/drafts — последние черновики; /show ID — показать\n"
        "/edit ID ТЕКСТ — заменить; /done ID — отметка ручной публикации\n"
        "/contentplan — темы и график; /queue — редакторская очередь; /cancel — отмена ввода\n"
        "/newbonds — будущие размещения; /checkbonds — проверить сейчас\n"
        "/whoami — ID чата\n\n"
        "Кнопки: На проверку → Одобрить → Опубликовать / Запланировать.\n"
        "Для редактирования нажми кнопку или ответь на черновик новым текстом.\n"
        "Автопубликация допустима только для ранее одобренного черновика.")


class InvestBot:
    def __init__(self, settings: Settings):
        settings.validate_bot()
        self.settings = settings
        self.store = DraftStore(settings.db_path)
        self.portfolio_store = PortfolioStore(settings.db_path)
        self.planner = ContentPlanner(self.portfolio_store,self.store)
        self.editorial_workflow = EditorialWorkflow(settings.db_path)
        self.placements = PlacementStore(settings.db_path)
        self.tech_news = TechNewsStore(settings.db_path)
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)

    def _generate_sync(self, kind: str):
        api=TInvest(self.settings.invest_token, self.settings.timezone)
        gen=PostGenerator(api, self.settings.account_ids, self.settings.cbr_path,
                          recent_papers=tuple(self.portfolio_store.recent_topics('paper')),
                          recent_glossary=tuple(self.portfolio_store.recent_topics('glossary')))
        today=datetime.now(self.settings.timezone).date()
        if kind=='glossary' and self.editorial_workflow.selection(today):
            from .lessons import TERMS
            from .model import Draft
            from .posts import current_week
            topic=self.editorial_workflow.selection(today)
            body=dict(TERMS).get(topic, f"Тема: {topic}\n\n[Объясни понятие простыми словами.]\n[Приведи пример и практический вывод.]")
            draft=Draft('glossary',current_week(today)[0].isoformat(),f"Ликбез за 2 минуты: {topic}\n\n{body}")
            return draft,topic
        draft=gen.generate(kind, today)
        return draft, (gen.selected_paper_uid if kind=='paper' else gen.selected_glossary_topic if kind=='glossary' else None)

    def _sync(self):
        api=TInvest(self.settings.invest_token, self.settings.timezone)
        return PortfolioSync(api, self.settings.account_ids, self.portfolio_store).run(
            datetime.now(self.settings.timezone).date(), days=14)

    def allowed(self, update: Update) -> bool:
        return bool(update.effective_chat and update.effective_chat.id == self.settings.telegram_chat_id
                    and update.effective_chat.type == 'private')

    async def guard(self, update: Update) -> bool:
        if self.allowed(update):
            return True
        if update.callback_query:
            await update.callback_query.answer('Доступ ограничен', show_alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text('Доступ ограничен.')
        return False

    @staticmethod
    def keyboard(row):
        state=row['state']; did=row['id']
        mk=lambda label, action: InlineKeyboardButton(label, callback_data=f'd:{action}:{did}')
        buttons=[]
        if state in ('draft','new','rejected'):
            buttons.append([mk('🔎 На проверку','review'),mk('✏️ Изменить','edit')])
        if state=='review':
            buttons.append([mk('✅ Одобрить','approve'),mk('✏️ Изменить','edit')])
        if state=='approved':
            buttons.append([mk('📢 Опубликовать','publish'),mk('🕒 Запланировать','schedule')])
            buttons.append([mk('✏️ Изменить','edit'),mk('↩️ На проверку','review')])
        if state=='scheduled':
            buttons.append([mk('↩️ Отменить расписание','unschedule'),mk('✏️ Изменить','edit')])
        if state in ('new','draft','review','approved','scheduled'):
            buttons.append([mk('🗑 Отклонить','reject')])
        return InlineKeyboardMarkup(buttons) if buttons else None

    async def send_draft(self, application: Application, did: int) -> None:
        row=self.store.get(did)
        if row is None or row['state']=='deleted':return
        label=f"📝 Черновик #{did} — {row['kind']} ({row['period']}) [{row['state']}]\n\n"
        body=row['body']
        if len(label+body)<=3900:
            msg=await application.bot.send_message(self.settings.telegram_chat_id,label+body,
                                                   reply_markup=self.keyboard(row))
        else:
            payload=io.BytesIO(body.encode('utf-8'));payload.name=f'draft_{did}.txt'
            msg=await application.bot.send_document(self.settings.telegram_chat_id,payload,
                       caption=label+'Текст длиннее лимита Telegram — приложен файлом.',
                       reply_markup=self.keyboard(row))
        self.store.link_message(msg.message_id,did)
        self.store.sent(did)

    async def publish(self, application: Application, did: int, *, scheduled=False) -> str:
        if not self.settings.telegram_channel_id:
            return 'Укажи TELEGRAM_CHANNEL_ID в .env и добавь бота администратором канала.'
        row=self.store.get(did)
        if not row:return 'Черновик отсутствует.'
        if len(row['body'])>4096:
            return 'Пост превышает 4096 символов Telegram. Сократи текст перед публикацией.'
        if not self.store.claim_publish(did,scheduled=scheduled):
            return 'Публикация недоступна: пост не одобрен, уже обработан или опубликован.'
        try:
            msg=await application.bot.send_message(self.settings.telegram_channel_id,row['body'])
        except Exception as exc:
            log.exception('Ошибка отправки поста #%s',did)
            self.store.publish_failure(did,f'{type(exc).__name__}: {exc}')
            return (f'Не удалось подтвердить публикацию #{did}. Статус «publishing» сохранён '
                    'во избежание дубля. Проверь канал вручную, прежде чем что-либо повторять.')
        self.store.publish_success(did,msg.message_id)
        return f'✅ Пост #{did} опубликован. ID сообщения: {msg.message_id}'

    async def tick_scheduled(self, app: Application) -> None:
        # Drafts created in the local web editor are delivered to the private chat.
        for row in self.store.unsent():
            try:
                await self.send_draft(app,row['id'])
            except Exception:
                log.exception('Не удалось отправить веб-черновик #%s',row['id'])
        # Web requests never send directly; a single bot process claims them.
        if self.settings.telegram_channel_id:
            for row in self.store.pending_publications():
                msg=await self.publish(app,row['id'])
                await app.bot.send_message(self.settings.telegram_chat_id,msg)
        for row in self.store.due_scheduled(datetime.now(timezone.utc).isoformat()):
            text=await self.publish(app,row['id'],scheduled=True)
            await app.bot.send_message(self.settings.telegram_chat_id,text)

    async def run_snapshot(self, app: Application) -> None:
        try:
            info=await asyncio.to_thread(self._sync)
            if info['warnings']:
                await app.bot.send_message(self.settings.telegram_chat_id,
                    '⚠️ Снимок портфеля сохранён с предупреждениями:\n'+'\n'.join(info['warnings'][:8]))
        except Exception as exc:
            log.exception('Снимок портфеля не создан')
            await app.bot.send_message(self.settings.telegram_chat_id,
                f'⚠️ Снимок портфеля не сохранён: {type(exc).__name__}')

    async def run_scheduled(self, kind: str, app: Application) -> None:
        now=datetime.now(self.settings.timezone).date()
        if self.editorial_workflow.states().get((now.isoformat(),kind))=='done':
            return
        try:
            draft,topic=await asyncio.to_thread(self._generate_sync,kind)
            did,fresh=self.store.insert(draft,scheduled=True)
            if fresh:
                if kind in ('paper','glossary'): self.portfolio_store.record_topic(kind,topic,did)
                await self.send_draft(app,did)
        except Exception as exc:
            log.exception('Не удалось сформировать %s',kind)
            try:
                await app.bot.send_message(self.settings.telegram_chat_id,
                    f'⚠️ Ошибка рубрики {kind}: {type(exc).__name__}. Проверь логи.')
            except Exception: log.exception('Не удалось отправить уведомление')

    async def startup(self, app: Application) -> None:
        for row in self.store.unsent():
            try: await self.send_draft(app,row['id'])
            except Exception: log.exception('Не удалось дослать черновик #%s',row['id'])
        if not self.settings.startup_catchup:return
        now=datetime.now(self.settings.timezone)
        due=[kind for kind, weekday, _ in WEEKLY_RULES if weekday == now.weekday()]
        if now.day==1:due.append('monthly')
        for kind in due:
            if (now.hour,now.minute)>=self.settings.times[kind]:
                await self.run_scheduled(kind,app)

    async def refresh_editorial_ideas(self) -> None:
        try:
            await asyncio.to_thread(self.editorial_workflow.refresh_ideas,datetime.now(self.settings.timezone).date())
        except Exception:
            log.exception('Не удалось обновить темы ликбеза')

    async def post_init(self, app: Application) -> None:
        self.scheduler.add_job(self.refresh_editorial_ideas,CronTrigger(day_of_week='mon',hour=9,minute=0,timezone=self.settings.timezone),
                               id='editorial-ideas',max_instances=1,coalesce=True)
        asyncio.create_task(self.refresh_editorial_ideas())
        for kind, _, weekday in WEEKLY_RULES:
            h,m=self.settings.times[kind]
            self.scheduler.add_job(self.run_scheduled,CronTrigger(day_of_week=weekday,hour=h,minute=m,timezone=self.settings.timezone),
                                   args=[kind,app],id=kind,max_instances=1,misfire_grace_time=3600,coalesce=True)
        h,m=self.settings.times['monthly']
        self.scheduler.add_job(self.run_scheduled,CronTrigger(day=1,hour=h,minute=m,timezone=self.settings.timezone),
                               args=['monthly',app],id='monthly',max_instances=1,misfire_grace_time=3600,coalesce=True)
        self.scheduler.add_job(self.tick_scheduled,'interval',minutes=1,args=[app],id='publishing',max_instances=1,coalesce=True)
        # Daily radar check; the existing 4-hour placement monitor stays in place too.
        self.scheduler.add_job(self.run_placements,CronTrigger(hour=8,minute=30,timezone=self.settings.timezone),
                               args=[app],id='radar-daily',max_instances=1,coalesce=True)
        self.scheduler.add_job(self.run_placements,'interval',hours=4,args=[app],id='placements',
                               max_instances=1,coalesce=True,misfire_grace_time=3600)
        import os
        if os.getenv('VK_AUTO_PUBLISH','false').lower() in ('1','true','yes'):
            for i,slot in enumerate(os.getenv('VK_NEWS_TIMES','10:00,13:00,16:00,19:00').split(',')):
                h,m=map(int,slot.strip().split(':'))
                self.scheduler.add_job(self.run_vk_news,CronTrigger(hour=h,minute=m,timezone=self.settings.timezone),
                                       id=f'vk-news-{i}',max_instances=1,coalesce=True,misfire_grace_time=600)
        self.scheduler.add_job(self.run_tech_news,'interval',hours=2,args=[app],id='tech-rss',
                               max_instances=1,coalesce=True,misfire_grace_time=3600)
        if self.settings.auto_snapshot:
            self.scheduler.add_job(self.run_snapshot,CronTrigger(hour=21,minute=0,timezone=self.settings.timezone),
                                   args=[app],id='daily-snapshot',max_instances=1,coalesce=True)
        self.scheduler.start()
        asyncio.create_task(self.startup(app))
        asyncio.create_task(self.run_placements(app))
        asyncio.create_task(self.run_tech_news(app))
        log.info('Бот запущен; расписание в %s', self.settings.timezone)

    async def run_tech_news(self, app: Application) -> None:
        import asyncio
        try:
            report=await asyncio.to_thread(TechNewsCollector(self.tech_news).scan)
            log.info('Tech RSS checked: %s',report)
        except Exception:
            log.exception('Ошибка автоматического сбора Tech RSS')

    async def run_vk_news(self) -> None:
        from .vk_news import run
        try:
            result=await asyncio.to_thread(run,self.settings.db_path,self.settings.timezone,publish=True,limit=1)
            log.info('VK news publish result: %s',result)
        except Exception:
            log.exception('VK news publish failed')

    async def post_shutdown(self, app: Application) -> None:
        if self.scheduler.running:self.scheduler.shutdown(wait=False)

    def _placement_scan_sync(self):
        api=TInvest(self.settings.invest_token,self.settings.timezone)
        return PlacementMonitor(self.placements,api,feed_url=self.settings.placements_feed_url,
                                finam_enabled=self.settings.placements_finam_enabled).scan()

    async def run_placements(self, app: Application) -> None:
        try:
            result=await asyncio.to_thread(self._placement_scan_sync)
            if result.get('busy'):
                return
            await self.notify_placement_updates(app)
        except Exception:
            log.exception('Ошибка обновления будущих размещений')
            try:
                await app.bot.send_message(self.settings.telegram_chat_id,
                    '⚠️ Проверка будущих размещений не выполнена. Посмотри логи или /checkbonds.')
            except Exception: log.exception('Не удалось уведомить о проверке размещений')

    def placements_keyboard(self, page: int = 0):
        rows=self.placements.upcoming(datetime.now(self.settings.timezone).date())
        size=8
        total=max(1,(len(rows)+size-1)//size)
        page=min(max(0,page),total-1)
        window=rows[page*size:(page+1)*size]
        buttons=[[InlineKeyboardButton(f"{p['placement_date'][5:]} · {p['name'][:37]}",
            callback_data=f"p:open:{p['id']}")] for p in window]
        controls=[]
        if page>0:controls.append(InlineKeyboardButton('◀️',callback_data=f'p:list:{page-1}'))
        controls.append(InlineKeyboardButton(f'{page+1}/{total}',callback_data='p:noop:0'))
        if page+1<total:controls.append(InlineKeyboardButton('▶️',callback_data=f'p:list:{page+1}'))
        buttons.append(controls)
        return ((f'🛰️ Будущие размещения ({len(rows)})\n'
                 'По дате размещения. Нажми на бумагу, чтобы посмотреть условия и создать черновик.'
                 if rows else '🛰️ В базе пока нет будущих размещений. Проверь /checkbonds. У API может не быть всех выпусков.'),
                InlineKeyboardMarkup(buttons))

    async def newbonds(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        text,keyboard=self.placements_keyboard()
        await update.effective_message.reply_text(text,reply_markup=keyboard)

    async def checkbonds(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        result=await asyncio.to_thread(self._placement_scan_sync)
        if result.get('busy'):
            await update.effective_message.reply_text('Проверка уже выполняется.');return
        summary='; '.join(f"{s['source']}: {s.get('count', 'ошибка')}" for s in result['sources'])
        await update.effective_message.reply_text(f'🛰️ Проверка завершена: {summary}\n/newbonds')
        # Manual scan also uses a notification round; prevents duplicate silent announcements.
        await self.notify_placement_updates(context.application)

    async def notify_placement_updates(self, app: Application):
        pending=self.placements.pending()
        if not pending:return
        names=[f"{p['placement_date']} · {p['name']}" for p in pending[:8]]
        suffix=f"\nИ ещё {len(pending)-8} выпусков." if len(pending)>8 else ''
        await app.bot.send_message(self.settings.telegram_chat_id,
            f"🛰️ Новые или изменённые размещения: {len(pending)}\n"+'\n'.join(names)+suffix,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🛰️ Открыть список',callback_data='p:list:0')]]))
        self.placements.mark_notified([(p['id'],p['version']) for p in pending])

    async def placement_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        q=update.callback_query
        await q.answer()
        try:
            _,action,raw=q.data.split(':')
            value=int(raw)
        except (ValueError,AttributeError):return
        if action=='noop':return
        if action=='list':
            text,keyboard=self.placements_keyboard(value)
            await q.edit_message_text(text,reply_markup=keyboard)
            return
        item=self.placements.get(value)
        if not item:
            await q.message.reply_text('Размещение уже прошло или исчезло из последней проверки.');return
        if action=='open':
            from .placements import render_draft
            text=render_draft(item)
            keyboard=InlineKeyboardMarkup([
                [InlineKeyboardButton('📝 Создать черновик',callback_data=f'p:draft:{value}')],
                [InlineKeyboardButton('↩️ К списку',callback_data='p:list:0')]])
            await q.message.reply_text(text[:3900],reply_markup=keyboard)
        elif action=='draft':
            did=await asyncio.to_thread(self.placements.create_draft,value)
            await q.message.reply_text(f'✅ Черновик #{did} добавлен в редакторскую очередь.')
            if not self.store.get(did)['sent_at']:
                await self.send_draft(context.application,did)
            else:
                await q.message.reply_text(f'Черновик #{did} уже был отправлен. /show {did}')

    async def button_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        choice=update.effective_message.text
        if choice == '🛰️ Новые размещения':
            await self.newbonds(update,context)
        elif choice == '◈ Инвестиции':
            await update.effective_message.reply_text(HELP)
        elif choice == '⚙ Tech & Gadgets':
            from .tech_store import TechDraftStore
            rows=TechDraftStore(self.settings.db_path).list_recent(15)
            message='⚙ Tech & Gadgets\n\n'+('\n'.join(f"#{r['id']} · {r['title']}" for r in rows) if rows else 'Черновиков пока нет.')
            await update.effective_message.reply_text(message+'\n\nРедактирование — на сайте Content Hub. Публикация Tech пока отключена.')


    async def whoami(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.guard(update):await update.effective_message.reply_text(f'ID чата: {update.effective_chat.id}')

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self.guard(update):
            await update.effective_message.reply_text(HELP,
                reply_markup=ReplyKeyboardMarkup([['◈ Инвестиции','⚙ Tech & Gadgets'],['🛰️ Новые размещения']],resize_keyboard=True))

    async def generate_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        kind=update.effective_message.text.split()[0].split('@')[0].lstrip('/')
        if kind not in RUBRICS:return
        try:
            draft,topic=await asyncio.to_thread(self._generate_sync,kind)
            did,created=self.store.insert(draft,scheduled=False)
            if kind in ('paper','glossary') and created:self.portfolio_store.record_topic(kind,topic,did)
            await self.send_draft(context.application,did)
        except Exception as exc:
            log.exception('Ошибка при генерации %s',kind)
            await update.effective_message.reply_text(f'⚠️ Ошибка создания: {type(exc).__name__}. Проверь логи.')

    async def contentplan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        plan=self.planner.overview(datetime.now(self.settings.timezone).date())
        items=plan['schedule'][:12]
        lines=['📅 Контент-план (ближайшие публикации):']
        lines += [f"{x['day']} · {x['label']} — {x['state']}" for x in items]
        paper=plan['suggested_paper']
        if paper:lines+=['',f"📊 Следующая бумага: {paper['name']} ({paper['ticker']}) — {paper['reason']}"]
        term=plan['suggested_term']
        if term:lines+=[f"📚 Термин: {term['title']}"]
        await update.effective_message.reply_text('\n'.join(lines)[:3900])

    async def queue(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        rows=[r for r in self.store.list_recent(25) if r['state'] not in ('published','rejected')]
        lines=['📋 Редакторская очередь:']+[f"#{r['id']} · {r['kind']} [{r['state']}]" for r in rows[:20]]
        await update.effective_message.reply_text('\n'.join(lines) if rows else 'Очередь пуста.')

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        context.user_data.pop('editing',None)
        context.user_data.pop('scheduling',None)
        await update.effective_message.reply_text('Редактирование/планирование отменено.')

    async def drafts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        rows=self.store.list_recent()
        await update.effective_message.reply_text('\n'.join(
            f"#{r['id']} — {r['kind']} ({r['period']}) [{r['state']}]" for r in rows) or 'Нет черновиков.')

    async def show(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        try:
            did=int(context.args[0]);row=self.store.get(did);assert row and row['state']!='deleted'
            await self.send_draft(context.application,did)
        except (IndexError,ValueError,AssertionError):
            await update.effective_message.reply_text('Используй /show 12')

    async def done(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        try:
            did=int(context.args[0]);row=self.store.get(did);assert row and row['state']!='deleted'
            if row['state']=='publishing':
                await update.effective_message.reply_text('Проверь канал: исход отправки неизвестен. /done подтвердит ручную публикацию.')
            self.store.mark_done(did)
            await update.effective_message.reply_text(f'#{did} отмечен как опубликованный вручную.')
        except (IndexError,ValueError,AssertionError):
            await update.effective_message.reply_text('Используй /done 12')

    def editable(self,did):
        row=self.store.get(did)
        return bool(row and row['state'] not in ('published','publishing','deleted'))

    async def edit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        parts=update.effective_message.text.split(maxsplit=2)
        if len(parts)<3 or not parts[1].isdigit() or not parts[2].strip():
            await update.effective_message.reply_text('Используй /edit 12 Новый текст');return
        did=int(parts[1])
        if not self.editable(did):
            await update.effective_message.reply_text('Черновик отсутствует или уже опубликован.');return
        self.store.set_body(did,parts[2].strip())
        self.store.change_state(did,'review') if self.store.get(did)['state'] in ('new','draft','rejected') else None
        await update.effective_message.reply_text(f'✅ Черновик #{did} обновлён. /show {did}')

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.guard(update):return
        q=update.callback_query
        await q.answer()
        try:
            _,action,raw=q.data.split(':');did=int(raw)
        except (ValueError,AttributeError):return
        row=self.store.get(did)
        if not row:
            await q.message.reply_text('Черновик не найден.');return
        if action=='edit':
            if not self.editable(did):
                await q.message.reply_text('Опубликованный черновик нельзя изменить.');return
            context.user_data['editing']=did
            context.user_data.pop('scheduling',None)
            await q.message.reply_text(f'Отправь новым сообщением полный текст черновика #{did}.')
            return
        if action=='schedule':
            if row['state']!='approved':
                await q.message.reply_text('Для планирования сначала одобри черновик.');return
            context.user_data['scheduling']=did
            context.user_data.pop('editing',None)
            await q.message.reply_text('Отправь дату и время в формате YYYY-MM-DD HH:MM (часовой пояс проекта).')
            return
        if action=='publish':
            message=await self.publish(context.application,did)
        else:
            target={'review':'review','approve':'approved','reject':'rejected','unschedule':'approved'}.get(action)
            if not target:return
            ok=self.store.change_state(did,target)
            message=f"✅ #{did}: {target}" if ok else 'Переход недоступен. Обнови черновик через /show.'
        await q.message.reply_text(message)
        try:
            await q.edit_message_reply_markup(reply_markup=self.keyboard(self.store.get(did)))
        except Exception:
            log.debug('Не удалось обновить кнопки',exc_info=True)

    async def reply_edit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.allowed(update):return
        msg=update.effective_message
        if not msg or not msg.text or msg.text.startswith('/'):return
        did=context.user_data.get('editing')
        scheduling=context.user_data.get('scheduling')
        if scheduling:
            try:
                local_dt=datetime.strptime(msg.text.strip(),'%Y-%m-%d %H:%M').replace(tzinfo=self.settings.timezone)
                if local_dt<=datetime.now(self.settings.timezone):raise ValueError('Дата должна быть в будущем')
                utc_dt=local_dt.astimezone(timezone.utc).isoformat()
                if not self.store.schedule(scheduling,utc_dt):raise ValueError('Пост должен быть одобрен')
            except ValueError as exc:
                await msg.reply_text('❌ '+str(exc)+'. Формат: 2026-11-02 10:00');return
            context.user_data.pop('scheduling',None)
            await msg.reply_text(f'🕒 Черновик #{scheduling} запланирован на {local_dt:%d.%m.%Y %H:%M}.')
            return
        if did is None and msg.reply_to_message:
            did=self.store.draft_for_message(msg.reply_to_message.message_id)
        if did is None:return
        if not self.editable(did):
            await msg.reply_text('Черновик уже опубликован либо заблокирован.');return
        self.store.set_body(did,msg.text.strip())
        if self.store.get(did)['state'] in ('new','draft','rejected'):
            self.store.change_state(did,'review')
        context.user_data.pop('editing',None)
        await msg.reply_text(f'✅ Текст #{did} обновлён. /show {did}')

    def run(self):
        app=(ApplicationBuilder().token(self.settings.telegram_token)
             .post_init(self.post_init).post_shutdown(self.post_shutdown).build())
        app.add_handler(CommandHandler(('start','help'),self.help))
        app.add_handler(CommandHandler('whoami',self.whoami))
        app.add_handler(CommandHandler('newbonds',self.newbonds))
        app.add_handler(CommandHandler('checkbonds',self.checkbonds))
        for kind in RUBRICS:app.add_handler(CommandHandler(kind,self.generate_command))
        for name,handler in [('drafts',self.drafts),('show',self.show),('edit',self.edit),('done',self.done),('contentplan',self.contentplan),('queue',self.queue),('cancel',self.cancel)]:
            app.add_handler(CommandHandler(name,handler))
        app.add_handler(CallbackQueryHandler(self.callback,pattern=r'^d:(review|approve|reject|edit|schedule|publish|unschedule):\d+$'))
        app.add_handler(CallbackQueryHandler(self.placement_callback,pattern=r'^p:(list|open|draft|noop):\d+$'))
        app.add_handler(MessageHandler(filters.Regex(r'^(🛰️ Новые размещения|◈ Инвестиции|⚙ Tech & Gadgets)$') & filters.TEXT,self.button_menu))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,self.reply_edit))
        app.run_polling(drop_pending_updates=False)
