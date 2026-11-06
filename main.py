import os, re, asyncio, logging
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["9180213043:AAG-v1YWsimemVqVsszdNgZ65Th5P-Y6V3w"]
MONGO_URI = os.environ["mongodb+srv://acfaaa13_db_user:wLFi9W0LOMbraIMo@cluster0.jofcmiu.mongodb.net/?appName=Cluster0"]
PORT = int(os.environ.get("PORT", 10000))

bot = Bot(TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)
db = AsyncIOMotorClient(MONGO_URI)["tgbot"]
_session = None


# ---------- أدوات مساعدة ----------
async def tg(method, **params):
    """نداء مباشر لـ Bot API (يدعم الحقول الحديثة: الوسوم وألوان الأزرار)."""
    global _session
    if _session is None:
        _session = aiohttp.ClientSession()
    async with _session.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=params) as r:
        return await r.json()


def norm(s):
    s = s.replace("ـ", "").translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي"}))
    s = re.sub(r"[()（）]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def btn(text, cb=None, style=None, url=None):
    b = {"text": text}
    if cb:
        b["callback_data"] = cb
    if url:
        b["url"] = url
    if style:
        b["style"] = style  # primary(أزرق) / success(أخضر) / danger(أحمر)
    return b


async def get_member(chat, user):
    r = await tg("getChatMember", chat_id=chat, user_id=user)
    return r.get("result") or {}


def has(member, right):
    return member.get("status") == "creator" or (member.get("status") == "administrator" and member.get(right))


# ---------- الأقفال ----------
LOCKS = {
    "chat": ["الدردشه"], "add": ["الاضافه"], "pin": ["التثبيت"], "tag": ["التاج"],
    "photos": ["الصور"], "stickers": ["الملصقات"], "audio": ["م صوت"], "files": ["الملفات"],
    "voice": ["ر صوت"], "vnote": ["فيديو مسج"], "videos": ["الفيديوهات"],
    "links": ["الروابط"], "mentions": ["المعرفات"],
    "polls": ["الاستفتئات", "الاستفتاءات"], "react": ["التفاعل"],
}
NAME2KEY = {n: k for k, names in LOCKS.items() for n in names}
PERM_FIELD = {
    "chat": "can_send_messages", "add": "can_invite_users", "pin": "can_pin_messages",
    "tag": "can_edit_tag", "photos": "can_send_photos", "stickers": "can_send_other_messages",
    "audio": "can_send_audios", "files": "can_send_documents", "voice": "can_send_voice_notes",
    "vnote": "can_send_video_notes", "videos": "can_send_videos", "polls": "can_send_polls",
}


def perms_from(locks):
    p = {f: not locks.get(k, False) for k, f in PERM_FIELD.items()}
    p["can_add_web_page_previews"] = True
    p["can_change_info"] = False
    return p


async def chat_locks(chat):
    return (await db.chats.find_one({"_id": chat}) or {}).get("locks", {})


# ---------- الصلاحيات ----------
RIGHTS = [
    ("info", "تغيير معلومات المجموعة"), ("delete", "حذف الرسائل"), ("ban", "حظر مستخدمين"),
    ("invite", "إضافة مستخدمين"), ("pin", "تثبيت الرسائل"), ("tags", "تحرير وسوم الأعضاء"),
    ("stories", "إدارة القصص"), ("welcome", "إدارة رسائل الترحيب"),
    ("live", "إدارة البثوث المباشرة"), ("promote", "إضافة أدمن جدد"), ("anon", "البقاء متخفيا"),
]


def promote_payload(chat, user, r):
    return dict(
        chat_id=chat, user_id=user, can_manage_chat=True,
        is_anonymous=r.get("anon", False),
        can_change_info=r.get("info", False), can_delete_messages=r.get("delete", False),
        can_restrict_members=r.get("ban", False), can_invite_users=r.get("invite", False),
        can_pin_messages=r.get("pin", False), can_manage_tags=r.get("tags", False),
        can_post_stories=r.get("stories", False), can_edit_stories=r.get("stories", False),
        can_delete_stories=r.get("stories", False),
        can_manage_video_chats=r.get("live", False), can_promote_members=r.get("promote", False),
    )


def rights_kb(chat, user, r):
    rows = []
    for key, label in RIGHTS:
        on = r.get(key, False)
        rows.append([btn(f"{'✔︎' if on else '×'} {label}", f"rt:{chat}:{user}:{key}", "success" if on else "danger")])
    rows.append([btn("تعديل الوسم", f"tg:{chat}:{user}", "success")])
    return {"inline_keyboard": rows}


async def can_edit_rights(clicker, chat, target_doc):
    m = await get_member(chat, clicker)
    if m.get("status") == "creator":
        return True
    # الأدمن الذي لديه صلاحية "إضافة أدمن جدد" يعدّل فقط أدمن ليست لديهم هذه الصلاحية
    return bool(has(m, "can_promote_members") and not target_doc.get("rights", {}).get("promote"))


# ---------- /start والرئيسية ----------
HELP = {
    "grp": "🛡 حماية المجموعة\nفتح/قفل الدردشة\nفتح/قفل الاضافة (إضافة مستخدمين)\nفتح/قفل التثبيت\nفتح/قفل التاج (تعديل الوسم)",
    "media": "🖼 حماية الميديا\nفتح/قفل: الصور – الملصقات (+GIF) – (م صوت) ملفات صوتية – الملفات – (ر صوت) رسائل صوتية – (فيديو مسج) – الفيديوهات – الروابط – المعرفات – الاستفتئات – التفاعل",
    "ban": "🔨 الحظر والكتم (بالرد على الشخص)\nحظر / فك الحظر\nكتم / فك الكتم",
    "adm": "👮 الأدمن\nرفع ادمن أو (اد) بالرد\nتعديل الصلاحيات بالرد على الأدمن\nوضع وسم + الاسم (بالرد)\n\nيجب رفع البوت أدمن بكامل الصلاحيات.",
}
MAIN_TEXT = "أهلاً بك 👋\nأنا بوت إدارة وحماية المجموعات.\nاختر قسماً لعرض الأوامر:"


def main_kb(username):
    return {"inline_keyboard": [
        [btn("🛡 حماية المجموعة", "help:grp"), btn("🖼 حماية الميديا", "help:media")],
        [btn("🔨 الحظر والكتم", "help:ban"), btn("👮 الأدمن", "help:adm")],
        [btn("➕ أضفني لمجموعتك", url=f"https://t.me/{username}?startgroup=true")],
    ]}


@router.message(CommandStart(), F.chat.type == "private")
async def start(m: Message):
    me = await bot.get_me()
    photos = await tg("getUserProfilePhotos", user_id=me.id, limit=1)
    kb = main_kb(me.username)
    ph = (photos.get("result") or {}).get("photos") or []
    if ph:
        await tg("sendPhoto", chat_id=m.chat.id, photo=ph[0][-1]["file_id"], caption=MAIN_TEXT, reply_markup=kb)
    else:
        await tg("sendMessage", chat_id=m.chat.id, text=MAIN_TEXT, reply_markup=kb)


@router.callback_query(F.data.startswith("help:"))
async def help_cb(c: CallbackQuery):
    key = c.data.split(":")[1]
    me = await bot.get_me()
    if key == "main":
        text, kb = MAIN_TEXT, main_kb(me.username)
    else:
        text, kb = HELP[key], {"inline_keyboard": [[btn("↩️ رجوع", "help:main")]]}
    method = "editMessageCaption" if c.message.photo else "editMessageText"
    key_name = "caption" if c.message.photo else "text"
    await tg(method, chat_id=c.message.chat.id, message_id=c.message.message_id, reply_markup=kb, **{key_name: text})
    await c.answer()


# ---------- أزرار الصلاحيات والوسم ----------
@router.callback_query(F.data.startswith(("ed:", "rt:", "tg:", "cx:")))
async def panel_cb(c: CallbackQuery):
    parts = c.data.split(":")
    act, chat, user = parts[0], int(parts[1]), int(parts[2])
    msg = c.message
    doc = await db.admins.find_one({"chat": chat, "user": user}) or {"rights": {}}

    if act == "cx":
        if c.from_user.id != user:
            return await c.answer("ليس لك", show_alert=True)
        await db.pending.delete_many({"chat": chat, "user": user})
        await tg("editMessageText", chat_id=msg.chat.id, message_id=msg.message_id, text="تم إلغاء الإجراء.")
        return await c.answer()

    if act == "tg":
        m = await get_member(chat, c.from_user.id)
        if not has(m, "can_manage_tags"):
            return await c.answer("ليست لديك صلاحية تحرير وسوم الأعضاء.", show_alert=True)
        await db.pending.update_one({"chat": chat, "user": c.from_user.id}, {"$set": {"target": user}}, upsert=True)
        await tg("sendMessage", chat_id=chat, text="يرجى ارسال الوسم",
                 reply_markup={"inline_keyboard": [[btn("إلغاء", f"cx:{chat}:{c.from_user.id}", "danger")]]})
        return await c.answer()

    if not await can_edit_rights(c.from_user.id, chat, doc):
        return await c.answer("ليست لديك صلاحية تعديل صلاحيات هذا الأدمن.", show_alert=True)

    rights = doc.get("rights", {})
    if act == "rt":
        key = parts[3]
        rights[key] = not rights.get(key, False)
        r = await tg("promoteChatMember", **promote_payload(chat, user, rights))
        if not r.get("ok"):
            return await c.answer(r.get("description", "فشل التعديل")[:190], show_alert=True)
        await db.admins.update_one({"chat": chat, "user": user}, {"$set": {"rights": rights}}, upsert=True)
    await tg("editMessageReplyMarkup", chat_id=msg.chat.id, message_id=msg.message_id,
             reply_markup=rights_kb(chat, user, rights))
    await c.answer()


# ---------- رسائل المجموعات ----------
async def reply(m, text, **kw):
    return await tg("sendMessage", chat_id=m.chat.id, text=text, reply_to_message_id=m.message_id, **kw)


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def group(m: Message):
    if not m.from_user:
        return
    chat, uid = m.chat.id, m.from_user.id
    text = m.text or m.caption or ""

    # انتظار إرسال الوسم
    if m.text:
        p = await db.pending.find_one_and_delete({"chat": chat, "user": uid})
        if p:
            r = await tg("setChatMemberTag", chat_id=chat, user_id=p["target"], tag=m.text.strip()[:16])
            return await reply(m, "تم تعديل الوسم ✅" if r.get("ok") else f"تعذر: {r.get('description')}")

    # فلترة الروابط والمعرفات
    locks = await chat_locks(chat)
    if locks.get("links") or locks.get("mentions"):
        kinds = {e.type for e in (m.entities or []) + (m.caption_entities or [])}
        bad = (locks.get("links") and kinds & {"url", "text_link"}) or \
              (locks.get("mentions") and kinds & {"mention", "text_mention"})
        if bad and (await get_member(chat, uid)).get("status") not in ("creator", "administrator"):
            try:
                await m.delete()
            except Exception:
                pass
            return

    t = norm(text)
    if not t:
        return
    issuer = None  # يُحمّل عند الحاجة

    async def member():
        nonlocal issuer
        if issuer is None:
            issuer = await get_member(chat, uid)
        return issuer

    # أقفال الحماية
    mt = re.match(r"^(فتح|قفل|غلق) (.+)$", t)
    if mt and mt.group(2) in NAME2KEY:
        if not has(await member(), "can_restrict_members"):
            return await reply(m, "ليست لديك صلاحية حظر المستخدمين.")
        key = NAME2KEY[mt.group(2)]
        if key == "react":
            return await reply(m, "تقييد التفاعلات غير متاح عبر Bot API في تلجرام.")
        lock = mt.group(1) != "فتح"
        locks[key] = lock
        await db.chats.update_one({"_id": chat}, {"$set": {f"locks.{key}": lock}}, upsert=True)
        if key in PERM_FIELD:
            r = await tg("setChatPermissions", chat_id=chat, permissions=perms_from(locks),
                         use_independent_chat_permissions=True)
            if not r.get("ok"):
                return await reply(m, f"تعذر: {r.get('description')}")
        return await reply(m, "تم القفل 🔒" if lock else "تم الفتح 🔓")

    # وضع وسم
    if t.startswith("وضع وسم ") and m.reply_to_message and m.reply_to_message.from_user:
        if not has(await member(), "can_manage_tags"):
            return await reply(m, "ليست لديك صلاحية تحرير وسوم الأعضاء.")
        r = await tg("setChatMemberTag", chat_id=chat, user_id=m.reply_to_message.from_user.id,
                     tag=t[len("وضع وسم "):].strip()[:16])
        return await reply(m, "تم وضع الوسم ✅" if r.get("ok") else f"تعذر: {r.get('description')}")

    if not m.reply_to_message or not m.reply_to_message.from_user:
        return
    target = m.reply_to_message.from_user

    if t in ("حظر", "فك الحظر", "كتم", "فك الكتم"):
        if not has(await member(), "can_restrict_members"):
            return await reply(m, "ليست لديك صلاحية حظر المستخدمين.")
        if t == "حظر":
            r = await tg("banChatMember", chat_id=chat, user_id=target.id)
        elif t == "فك الحظر":
            r = await tg("unbanChatMember", chat_id=chat, user_id=target.id, only_if_banned=True)
        elif t == "كتم":
            r = await tg("restrictChatMember", chat_id=chat, user_id=target.id, use_independent_chat_permissions=True,
                         permissions={f: False for f in list(PERM_FIELD.values()) + ["can_add_web_page_previews"]})
        else:
            r = await tg("restrictChatMember", chat_id=chat, user_id=target.id, use_independent_chat_permissions=True,
                         permissions=perms_from(locks))
        return await reply(m, "تم ✅" if r.get("ok") else f"تعذر: {r.get('description')}")

    if t in ("رفع ادمن", "اد"):
        if not has(await member(), "can_promote_members"):
            return await reply(m, "ليست لديك صلاحية إضافة أدمن جدد.")
        # تلجرام يعتبر "كل الصلاحيات False" خفضاً للرتبة، لذلك تُمنح صلاحية البثوث كحد أدنى
        rights = {"live": True}
        r = await tg("promoteChatMember", **promote_payload(chat, target.id, rights))
        if not r.get("ok"):
            return await reply(m, f"تعذر الرفع: {r.get('description')}")
        await db.admins.update_one({"chat": chat, "user": target.id},
                                   {"$set": {"rights": rights, "promoted_by": uid}}, upsert=True)
        return await reply(m, "تم رفعه ادمن ✅", reply_markup={"inline_keyboard": [[
            btn("تعديل الصلاحيات", f"ed:{chat}:{target.id}", "primary")]]})

    if t == "تعديل الصلاحيات":
        doc = await db.admins.find_one({"chat": chat, "user": target.id})
        if not doc:
            return await reply(m, "هذا الأدمن لم يُرفع بواسطة البوت، لا يمكن تعديل صلاحياته.")
        if not await can_edit_rights(uid, chat, doc):
            return await reply(m, "ليست لديك صلاحية تعديل صلاحيات هذا الأدمن.")
        await reply(m, "تعديل الصلاحيات", reply_markup=rights_kb(chat, target.id, doc.get("rights", {})))


# ---------- التشغيل ----------
async def health(_):
    return web.Response(text="ok")


async def main():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
