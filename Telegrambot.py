from dotenv import load_dotenv
load_dotenv()
import os
import logging
import asyncio

# محاولة استيراد nest_asyncio لتجنب مشكلة "This event loop is already running"
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    print("WARNING: يُفضل تثبيت مكتبة nest_asyncio عبر 'pip install nest_asyncio' لتجنب مشاكل حلقة الأحداث.")

from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from tradingview_ta import TA_Handler, Interval
from keep_alive import keep_alive

# إعداد logging للتصحيح
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# تحميل التوكن من ملف البيئة
TOKEN = os.getenv("TELEGRAM_TOKEN")

# --- إعدادات القناة والدعوات ---
CHANNEL_USERNAME = "@NADEEB102"  # ضع اسم القناة بصيغة @username
REFERRAL_BASE = "https://t.me/Nadeeb_Alert_bot?start="  # رابط الدعوة (يُضاف إليه معرف المستخدم)
REQUIRED_INVITES = 0  # عدد الدعوات المطلوبة (استخدم 0 للسماح بالاستخدام الفوري)

# القواميس لتسجيل بيانات الدعوات في الذاكرة:
invited_users = {}   # المستدعى -> referrer
referrals = {}       # referrer -> مجموعة من المستدعى

# --------------------
# إعدادات التنبيهات والاختيارات
# --------------------
SCREENER_OPTIONS = {
    "1": "forex",
    "2": "crypto",
    "3": "cfd",
    "4": "indices",
    "5": "stocks"
}
EXCHANGE_OPTIONS = {
    "1": "OANDA",
    "2": "BINANCE",
    "3": "FX",
    "4": "PEPPERSTONE",
    "5": "FOREXCOM",
    "6": "TVC",
    "7": "CAPITALCOM",
    "8": "BITFINEX",
    "9": "KRAKEN",
    "10": "COINBASE",
    "11": "BITSTAMP",
    "12": "CRYPTOCAP",
    "13": "MEXC"
}

alerts = {}
alert_counter = 1  # عداد للتنبيهات الفريدة

CHECK_INTERVAL = 29  # فترة فحص الأسعار (بالثواني)

# مراحل المحادثة لإنشاء التنبيه
SELECT_SCREEN, SELECT_EXCHANGE, ENTER_SYMBOL, SELECT_CANDIDATE, ENTER_TARGET = range(5)

# --------------------
# ديكوريتر للتحقق من عضوية المستخدم في القناة
# --------------------
def require_channel_membership(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        try:
            member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
            if member.status not in ['creator', 'administrator', 'member']:
                raise Exception
        except Exception:
            await update.message.reply_text(f"⚠️ يجب عليك الانضمام للقناة التالية أولاً: {CHANNEL_USERNAME}")
            # إذا كانت داخل محادثة حوارية (ConversationHandler)، ننهى المحادثة
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapper

# --------------------
# دوال البحث عن رموز العملة
# --------------------
def generate_candidate_symbols(symbol: str):
    """
    توليد مجموعة من المرشحات لرمز العملة المُدخل.
    """
    candidates = set()
    normalized = symbol.strip().upper()
    candidates.add(normalized)
    if "USD" not in normalized:
        candidates.add(normalized + "USD")
    candidates.add(normalized.lower())
    return list(candidates)

def search_symbol_across_all(symbol: str):
    """
    البحث عن رمز العملة في عدة screeners وexchanges باستخدام المرشحات.
    يعيد قائمة من التركيبات كـ (candidate, screener, exchange).
    """
    candidates = generate_candidate_symbols(symbol)
    
    screeners = [
        "crypto",
        "forex",
        "cfd",
        "indices",
        "america"  # الأسهم الأمريكية
    ]
    
    exchanges = [
        "OANDA",
        "BINANCE",
        "FX",
        "PEPPERSTONE",
        "FOREXCOM",
        "TVC",
        "CAPITALCOM",
        "BITFINEX",
        "KRAKEN",
        "COINBASE",
        "BITSTAMP"
        "CRYPTOCAP",
        "MEXC",
    ]
    
    results = []  # لتخزين التركيبات الناجحة
    for screener in screeners:
        for exchange in exchanges:
            for candidate in candidates:
                try:
                    handler = TA_Handler(
                        symbol=candidate,
                        screener=screener,
                        exchange=exchange,
                        interval=Interval.INTERVAL_5_MINUTES
                    )
                    handler.get_analysis()
                    results.append((candidate, screener, exchange))
                    break
                except Exception:
                    continue
    return results

# --------------------
# أوامر البوت
# --------------------
@require_channel_membership
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    display_name = user.username if user.username else user.first_name

    # معالجة معلمة رابط الدعوة إن وُجدت
    args = context.args
    if args:
        try:
            referrer_id = int(args[0])
        except ValueError:
            await update.message.reply_text("⚠️ رابط الدعوة غير صالح.")
            return
        if referrer_id == user_id:
            await update.message.reply_text("⚠️ لا يمكنك استخدام رابط الدعوة الخاص بك.")
            return
        if user_id not in invited_users:
            invited_users[user_id] = referrer_id
            if referrer_id not in referrals:
                referrals[referrer_id] = set()
            referrals[referrer_id].add(user_id)
            invite_count = len(referrals[referrer_id])
            remaining = REQUIRED_INVITES - invite_count
            if remaining > 0:
                message_text = f"✅ {display_name} قام بالدخول عبر رابط دعوتك! لم يتبقى لك سوى {remaining} دعوة."
            else:
                message_text = f"✅ {display_name} قام بالدخول عبر رابط دعوتك! الآن يمكنك استخدام البوت."
            try:
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=message_text
                )
            except Exception as e:
                logger.error(f"خطأ في إرسال رسالة للمُحيل {referrer_id}: {e}")

    # صياغة النص بناءً على عدد الدعوات المطلوبة للمستخدم نفسه
    if REQUIRED_INVITES == 0:
        welcome_text = (
            f"أهلاً {display_name}!\n\n"
            "بما أن المالك لا يطلب دعوة، فيمكنك استخدام البوت الآن.\n"
            "استخدم /info للتعرف على التعليمات و /alert لإنشاء تنبيه."
        )
    elif user_id in referrals and len(referrals[user_id]) >= REQUIRED_INVITES:
        welcome_text = (
            f"أهلاً {display_name}!\n\n"
            "تم تفعيل دخولك بنجاح ويمكنك الآن استخدام البوت.\n"
            "استخدم /info للتعرف على التعليمات و /alert لإنشاء تنبيه."
        )
    else:
        referral_link = f"{REFERRAL_BASE}{user_id}"
        invite_text = f"{REQUIRED_INVITES} شخص" if REQUIRED_INVITES == 1 else f"{REQUIRED_INVITES} أشخاص"
        welcome_text = (
            f"أهلاً {display_name}!\n\n"
            f"تم تفعيل دخولك، ولكن لا يمكنك استخدام البوت حتى تقوم بدعوة {invite_text} على الأقل.\n"
            f"شارك رابط الدعوة الخاص بك مع أصدقائك:\n{referral_link}"
        )
    await update.message.reply_text(welcome_text)

async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info_text = (
        "بفضل فريق طلاب هيرمس، أصبح لديكم الآن أول بوت لتلقي تنبيهات ترايدينغ فيو بكل دقة وبالمجان، دون أي حدود.\n\n"
        "عند إنشاء تنبيه باستخدام أمر /alert، ستتبع الخطوات التالية:\n\n"
        "1. اختر نوع السوق (screener):\n"
        "   - فكّر بهذا الخيار كأنه 'تصنيف' للسوق ويُعرض بجانب رمز العملة.\n"
        "   - مثال: إذا كان رمز التنبيه الذي تريد استخدامه مثل eurusd، فهذا يعني أنه ينتمي لسوق الفوركس، لذا عليك اختيار forex.\n\n"
        "2. اختر منصة التداول (exchange):\n"
        "   - هذا الخيار يعني 'البروكر' الذي يتم التحليل عليه.\n"
        "   - مثال: إذا كنت تريد تنبيهًا على eurusd، فتأكد من اختيار منصة تداول تدعم الفوركس مثل forexcom أو oanda، حسب الخيارات المتاحة.\n\n"
        "3. إذا أخطأت في خطوة ما:\n"
        "   - لا تقلق! إذا أدخلت رمزًا غير صحيح أو اخترت خيارًا خاطئًا، سيبحث البوت عن الأماكن الممكنة لوضع تنبيهك، وسيعرض عليك قائمة منها لتختار الخيار المناسب."
    )
    await update.message.reply_text(info_text)

# ----------------------------------
# محادثة /alert لجمع بيانات التنبيه
# ----------------------------------
@require_channel_membership
async def alert_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # التحقق من شرط الدعوات إذا كان مفروضاً
    if not (user_id in referrals and len(referrals[user_id]) >= REQUIRED_INVITES) and REQUIRED_INVITES != 0:
        invite_text = f"{REQUIRED_INVITES} شخص" if REQUIRED_INVITES == 1 else f"{REQUIRED_INVITES} أشخاص"
        await update.message.reply_text(f"⚠️ لا يمكنك استخدام التنبيهات حتى تقوم بدعوة {invite_text} على الأقل.")
        return ConversationHandler.END

    options_text = "لإنشاء تنبيه جديد، اختر نوع الـ screener بإدخال الرقم المناسب:\n"
    for num, option in SCREENER_OPTIONS.items():
        options_text += f"{num}. {option}\n"
    await update.message.reply_text(options_text)
    return SELECT_SCREEN

async def select_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    if choice not in SCREENER_OPTIONS:
        await update.message.reply_text("❌ الخيار غير صحيح. الرجاء إدخال رقم من الخيارات المتاحة.")
        return SELECT_SCREEN
    context.user_data["screener"] = SCREENER_OPTIONS[choice]
    options_text = "الآن اختر الـ exchange بإدخال الرقم المناسب:\n"
    for num, option in EXCHANGE_OPTIONS.items():
        options_text += f"{num}. {option}\n"
    await update.message.reply_text(options_text)
    return SELECT_EXCHANGE

async def select_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    if choice not in EXCHANGE_OPTIONS:
        await update.message.reply_text("❌ الخيار غير صحيح. الرجاء إدخال رقم من الخيارات المتاحة.")
        return SELECT_EXCHANGE
    context.user_data["exchange"] = EXCHANGE_OPTIONS[choice]
    await update.message.reply_text("أدخل رمز العملة (مثلاً: BTCUSDT أو XAUUSD):")
    return ENTER_SYMBOL

async def enter_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = update.message.text.strip()
    if not symbol:
        await update.message.reply_text("❌ يرجى إدخال رمز العملة بشكل صحيح.")
        return ENTER_SYMBOL
    screener = context.user_data["screener"]
    exchange = context.user_data["exchange"]
    try:
        handler = TA_Handler(
            symbol=symbol.upper(),
            screener=screener,
            exchange=exchange,
            interval=Interval.INTERVAL_5_MINUTES
        )
        handler.get_analysis()
        context.user_data["symbol"] = symbol.upper()
        await update.message.reply_text("تم التحقق من رمز العملة بنجاح.\nأدخل السعر الهدف للتنبيه:")
        return ENTER_TARGET
    except Exception as e:
        logger.error(f"فشل التحقق من رمز {symbol} باستخدام الخيارات [{screener}, {exchange}]: {e}")
        results = search_symbol_across_all(symbol)
        if not results:
            await update.message.reply_text(f"⚠️ حدث خطأ في جلب بيانات {symbol} باستخدام الخيارات [{screener}, {exchange}].\nلم يتم العثور على هذه العملة.")
            return ConversationHandler.END
        context.user_data["candidates"] = results
        msg = (f"⚠️ لم يتم العثور على {symbol} باستخدام الخيارات [{screener}, {exchange}].\n"
               "يمكن أن توجد العملة في الخيارات التالية:\n")
        for idx, (cand, cand_screener, cand_exchange) in enumerate(results, start=1):
            msg += f"{idx}. رمز: {cand} | Screener: {cand_screener} | Exchange: {cand_exchange}\n"
        msg += "الرجاء اختيار الخيار المناسب بإدخال رقم الخيار."
        await update.message.reply_text(msg)
        return SELECT_CANDIDATE

async def select_candidate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    try:
        index = int(choice)
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال رقم صالح.")
        return SELECT_CANDIDATE
    candidates = context.user_data.get("candidates", [])
    if not candidates or index < 1 or index > len(candidates):
        await update.message.reply_text("❌ الخيار غير صالح. الرجاء إدخال رقم من القائمة.")
        return SELECT_CANDIDATE
    chosen = candidates[index - 1]
    context.user_data["symbol"] = chosen[0]
    context.user_data["screener"] = chosen[1]
    context.user_data["exchange"] = chosen[2]
    await update.message.reply_text(
        f"تم اختيار العملة: {chosen[0]} باستخدام الخيارات: screener: {chosen[1]}, exchange: {chosen[2]}.\n"
        "أدخل السعر الهدف للتنبيه:"
    )
    return ENTER_TARGET

async def enter_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_price = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال قيمة رقمية للسعر الهدف.")
        return ENTER_TARGET
    context.user_data["target_price"] = target_price
    return await confirm_alert(update, context)

async def confirm_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global alert_counter
    screener = context.user_data["screener"]
    exchange = context.user_data["exchange"]
    symbol = context.user_data["symbol"]
    target_price = context.user_data["target_price"]
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    alert_id = alert_counter
    alert_counter += 1

    confirm_text = (
        f"تم إنشاء تنبيه رقم {alert_id} للعملة {symbol} عند السعر {target_price}.\n"
        f"التفاصيل:\n"
        f"• Screener: {screener}\n"
        f"• Exchange: {exchange}\n"
        "سوف يقوم البوت بمراقبة السعر وإرسال التنبيه عند بلوغ الهدف."
    )
    await update.message.reply_text(confirm_text)

    alert_obj = {
        "alert_id": alert_id,
        "screener": screener,
        "exchange": exchange,
        "symbol": symbol,
        "target_price": target_price,
        "chat_id": chat_id,
        "user_id": user_id
    }
    alerts[alert_id] = alert_obj
    logger.info(f"تم إضافة تنبيه جديد (رقم {alert_id}) للدردشة {chat_id}: {symbol} عند {target_price}")
    return ConversationHandler.END

async def alert_cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم إلغاء إنشاء التنبيه.")
    return ConversationHandler.END

# ---------------------------------------------
# تنفيذ أمر /cancel لإلغاء تنبيه موجود برقم معين
# ---------------------------------------------
@require_channel_membership
async def cancel_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("❌ يرجى تحديد رقم التنبيه المراد إلغاؤه.\nمثال: /cancel 23")
        return
    try:
        alert_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ رقم التنبيه غير صالح.")
        return
    alert_obj = alerts.get(alert_id)
    if not alert_obj:
        await update.message.reply_text("❌ لا يوجد تنبيه بهذا الرقم.")
        return
    if update.effective_user.id != alert_obj["user_id"]:
        await update.message.reply_text("❌ ليس لديك الصلاحية لإلغاء هذا التنبيه.")
        return
    del alerts[alert_id]
    await update.message.reply_text(f"✅ تم إلغاء التنبيه رقم {alert_id}.")
    logger.info(f"تنبيه رقم {alert_id} تم إلغاؤه من قبل المستخدم {update.effective_user.id}.")

# -----------------------------
# مهمة فحص الأسعار بشكل دوري
# -----------------------------
async def check_prices(context: ContextTypes.DEFAULT_TYPE):
    for alert_id, alert_obj in list(alerts.items()):
        symbol = alert_obj["symbol"]
        screener = alert_obj["screener"]
        exchange = alert_obj["exchange"]
        target_price = alert_obj["target_price"]
        chat_id = alert_obj["chat_id"]
        try:
            handler = TA_Handler(
                symbol=symbol,
                screener=screener,
                exchange=exchange,
                interval=Interval.INTERVAL_5_MINUTES
            )
            analysis = handler.get_analysis()
            indicators = analysis.indicators
            high_price = float(indicators.get('high', 0))
            low_price = float(indicators.get('low', 0))
            # حذف التنبيه عند تفعيل السعر
            if low_price <= target_price <= high_price:
                context.application.create_task(
                    context.application.bot.send_message(
                        chat_id=chat_id,
                        text=f"⚠️ تنبيه رقم {alert_id}: تم تفعيل التنبيه للعملة {symbol} عند السعر {target_price}."
                    )
                )
                del alerts[alert_id]
                logger.info(f"تم تفعيل التنبيه رقم {alert_id} لـ {symbol} عند {target_price} في الدردشة {chat_id}")
        except Exception as e:
            logger.error(f"خطأ في جلب بيانات {symbol} ({screener}, {exchange}): {e}")
            # لن يتم إرسال إشعار للمستخدم، والتنبيه سيبقى محفوظاً لإعادة المحاولة لاحقاً.

# -----------------------------
# إعداد قائمة الأوامر (عند كتابة /)
# -----------------------------
async def set_commands(application):
    commands = [
        BotCommand("start", "بدء البوت والتحقق من الشروط"),
        BotCommand("info", "تعليمات استخدام البوت"),
        BotCommand("alert", "إنشاء تنبيه جديد"),
        BotCommand("cancel", "إلغاء تنبيه برقم التنبيه")
    ]
    await application.bot.set_my_commands(commands)
    logger.info("تم إعداد أوامر البوت.")

# -----------------------------
# التشغيل الرئيسي للبوت
# -----------------------------
async def main():
    keep_alive()
    app = ApplicationBuilder().token(TOKEN).build()

    await set_commands(app)

    alert_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("alert", alert_start)],
        states={
            SELECT_SCREEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_screen)],
            SELECT_EXCHANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_exchange)],
            ENTER_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_symbol)],
            SELECT_CANDIDATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_candidate)],
            ENTER_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_target)]
        },
        fallbacks=[CommandHandler("cancel", alert_cancel_conversation)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("info", info))
    app.add_handler(alert_conv_handler)
    app.add_handler(CommandHandler("cancel", cancel_alert))

    app.job_queue.run_repeating(check_prices, interval=CHECK_INTERVAL, first=10)

    logger.info("البوت يعمل...")
    await app.run_polling(close_loop=False)

if __name__ == "__main__":
    asyncio.run(main())
