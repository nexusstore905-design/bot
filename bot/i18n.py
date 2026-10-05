"""Customer-facing text in English and Urdu.

Admin and supplier screens stay in English. Use t(lang, key, **params).
"""
import html

LANGUAGES = {"en": "English", "ur": "اردو"}
DEFAULT_LANGUAGE = "en"

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        # Buttons
        "btn_new_order": "✨ New order",
        "btn_my_orders": "📦 My orders",
        "btn_balance": "💰 Balance",
        "btn_reorder": "🔁 Order again",
        "btn_support": "🆘 Support",
        "btn_help": "💬 Help",
        "btn_language": "🌐 Language",
        "btn_logout": "🔓 Sign out",
        "btn_menu": "🏠 Menu",
        "btn_cancel": "✖ Cancel",
        "btn_back": "◀ Back",
        "btn_products": "◀ Products",
        "btn_packages": "◀ Packages",
        "btn_checkout": "✅ Checkout",
        "btn_add_more": "➕ Add more",
        "btn_clear_cart": "🗑 Clear cart",
        "btn_cart": "🛒 Cart · {count}",
        "btn_submit": "🚀 Submit order",
        "btn_start_over": "↩️ Start over",
        "btn_change_pid": "✏️ Change Player ID",
        "btn_refresh": "🔄 Refresh",
        "btn_get_help": "🆘 Get help",
        "btn_forget_pids": "🗑 Forget saved IDs",
        "btn_qty": "Qty {qty}",
        # Home
        "home_welcome_back": "Welcome back, {name}",
        "home_welcome": "Welcome, {name}",
        "home_stats": "{active} active · {due} awaiting payment",
        "home_last": "Last order <code>{order_id}</code> · {status}",
        "home_empty": "Tap <b>New order</b> to place your first order.",
        # Sign in
        "signin_title": "🔐 <b>Member sign in</b>",
        "signin_body": "This bot is for invited members. Send the access code from your administrator.",
        "signin_note": "Your code message is deleted after you send it.",
        "signin_ok": "✅ <b>You're in, {name}!</b>",
        "code_failed": "❌ <b>That code didn't work</b>",
        "code_invalid": "Invalid access code. {remaining} attempt(s) left.",
        "code_locked": "Too many wrong attempts. Try again in {minutes} min.",
        "access_revoked": "⛔ Your access has been revoked. Contact the administrator.",
        "account_locked": "🔒 Too many wrong codes. Try again in {minutes} min.",
        "signed_out": "🔓 <b>Signed out</b>\n\nSend /start to sign in again.",
        "need_signin": "🔐 Send /start to sign in first.",
        "slow_down": "⏳ One moment — try that again.",
        "maintenance": "🚧 <b>Maintenance</b>\n\nThe bot is paused for maintenance. Please check back soon.",
        # Language
        "lang_pick": "🌐 <b>Choose your language</b>",
        "lang_set": "✅ Language set to English.",
        # Order flow
        "new_order": "New order",
        "step": "Step {step} of {total}",
        "choose_product": "Choose a product",
        "choose_product_hint": "Tap a product, or type a package name to search.",
        "choose_package": "Tap a package to add it to your cart.",
        "choose_qty": "How many?",
        "added_toast": "Added to cart ✓",
        "no_products": "😕 No products right now. Check back soon.",
        "product_gone": "❌ That product is no longer available.",
        "search_results": "🔎 Results for “{query}”",
        "search_none": "No packages match “{query}”. Try another word.",
        "cart_title": "🛒 <b>Your cart</b>",
        "cart_hint": "Adjust quantities, add more, or check out.",
        "cart_empty": "🛒 Your cart is empty.",
        "cart_changed": "🛒 Your cart changed. Review it again.",
        "qty_range": "Choose 1 to 99.",
        "qty_hint": "Use + / − to change the quantity.",
        "pid_title": "Your Player ID",
        "pid_prompt": "Send your Player ID. Double-check it before submitting.",
        "pid_saved_hint": "Or tap a recent ID:",
        "pid_invalid": "❌ That Player ID doesn't look right. Use 3–20 characters with no spaces.",
        "pids_cleared": "Saved IDs cleared.",
        "session_expired": "⌛ Your order session expired. Start a new order from the menu.",
        "review_title": "🧾 <b>Review your order</b>",
        "review_hint": "Check everything, then submit.",
        "player_id": "Player ID",
        "checking": "⏳ Checking your order…",
        "selection_changed": "⚠️ Some packages are no longer available. Please start again.",
        "no_supplier": "⚠️ We can't take this order right now — a package has no supplier yet. The admin has been told; nothing was submitted.",
        "limit_title": "🚫 <b>Order limit reached</b>",
        "limit_blocked": "Ordering is turned off for your account.",
        "limit_daily": "You've reached your daily limit ({limit} orders). Try again tomorrow.",
        "create_failed": "⚠️ We couldn't create your order. Try again in a moment.",
        "order_received": "⏳ Order received — sending to suppliers…",
        "sent_title": "✅ Order sent",
        "sent_hint": "We'll message you here when it's done.",
        "retry_title": "⏳ Order saved — delivery retrying",
        "retry_hint": "We'll keep trying automatically. Please don't submit it again.",
        "attention_title": "⚠️ Order saved — needs attention",
        "attention_hint": "The admin has been notified. Please don't submit it again.",
        "reorder_missing": "No longer available, left out: {names}",
        "reorder_none": "😕 Those packages are no longer available.",
        "order_not_found": "⚠️ That order couldn't be found.",
        "order_cancelled_flow": "✖ Order cancelled. Your cart was cleared.",
        "menu_cart_cleared": "Your unfinished cart was cleared.",
        # Order list and card
        "orders_title": "📦 <b>Your orders</b>",
        "orders_page": "Page {page} of {pages}",
        "orders_empty": "📭 No orders yet. Place your first one from the menu.",
        "more_items": "+{count} more",
        "order": "Order",
        "items": "Items",
        "st_pending": "Waiting for supplier",
        "st_processing": "Partly done",
        "st_completed": "Completed",
        "st_failed": "Needs support",
        "st_cancelled": "Cancelled",
        "tl_placed": "Placed",
        "tl_sent": "Sent to supplier",
        "tl_retrying": "Retrying delivery",
        "tl_working": "Supplier working",
        "tl_delivered": "Delivered",
        "tl_failed": "Needs support",
        "tl_cancelled": "Cancelled",
        "proof_received": "📸 Delivery proof received",
        # Notices
        "n_completed": "🎉 <b>Your order is complete</b>",
        "n_failed": "❌ <b>Your order needs support</b>\nA supplier couldn't complete it. Our team has been notified.",
        "n_cancelled": "🚫 <b>Your order was cancelled</b>",
        "n_auto_cancel": "⏱️ <b>Order auto-cancelled</b>\nThe supplier didn't respond within {minutes} minutes. You can order again anytime.",
        "n_partial_timeout": "⚠️ <b>Part of your order timed out</b>\nCompleted: {done}. Support has been notified about the rest.",
        "n_group_timeout": "⚠️ <b>One supplier group timed out</b>\nThe rest of your order is still being processed.",
        "n_group_issue": "⚠️ <b>One supplier group reported an issue</b>\nThe rest of your order continues, and support has been notified.",
        "n_proof": "📸 <b>Delivery proof</b> for order <code>{order_id}</code>",
        # Balance
        "balance_title": "💰 <b>Your balance</b>",
        "balance_due": "Completed orders awaiting payment: <b>{count}</b>",
        "balance_last": "Last payment cleared: {date}",
        "never": "never",
        "balance_hint": "Your administrator confirms payments.",
        # Support
        "support_title": "🆘 <b>Contact support</b>",
        "support_about": "About order <code>{order_id}</code>",
        "support_prompt": "Send your message in one text. An admin will reply right here.",
        "support_too_long": "Please keep it under {limit} characters.",
        "support_sent": "✅ <b>Message sent.</b> An admin will reply here soon.",
        "support_failed": "⚠️ Support can't be reached right now. Try again later.",
        "support_cancelled": "✖ Support request cancelled.",
        "support_reply": "💬 <b>Support reply</b>",
        # Help and about
        "help": (
            "💬 <b>Help</b>\n\n"
            "<b>Place an order</b>\n"
            "1. Tap <b>New order</b> and pick a product.\n"
            "2. Pick a package and quantity.\n"
            "3. Send your Player ID (or tap a recent one).\n"
            "4. Review and submit.\n\n"
            "<b>Order status</b>\n"
            "⏳ Waiting for supplier · ⚙️ Partly done · ✅ Completed\n"
            "❌ Needs support · 🚫 Cancelled\n\n"
            "Finished orders have <b>Order again</b>. Questions? Tap <b>Support</b>.\n\n"
            "/start menu · /myorders orders · /support help · /language language"
        ),
        "about": (
            "🏪 <b>About {store}</b>\n\n"
            "Place orders and follow them live, right here in Telegram.\n"
            "Each order goes straight to the supplier for its product, and you get a message the moment it's done."
        ),
        # Bot profile and command menu
        "bot_description": "Order top-ups and track them live. Invited members only — ask your administrator for an access code.",
        "bot_short_description": "Order top-ups and track them live.",
        "cmd_start": "Open the menu",
        "cmd_myorders": "Your orders",
        "cmd_support": "Contact support",
        "cmd_language": "Change language",
        "cmd_help": "How it works",
        "cmd_cancel": "Cancel the current step",
        "cmd_logout": "Sign out",
    },
    "ur": {
        "btn_new_order": "✨ نیا آرڈر",
        "btn_my_orders": "📦 میرے آرڈرز",
        "btn_balance": "💰 بیلنس",
        "btn_reorder": "🔁 دوبارہ آرڈر",
        "btn_support": "🆘 مدد",
        "btn_help": "💬 رہنمائی",
        "btn_language": "🌐 زبان",
        "btn_logout": "🔓 سائن آؤٹ",
        "btn_menu": "🏠 مینو",
        "btn_cancel": "✖ منسوخ",
        "btn_back": "◀ واپس",
        "btn_products": "◀ پروڈکٹس",
        "btn_packages": "◀ پیکجز",
        "btn_checkout": "✅ آگے بڑھیں",
        "btn_add_more": "➕ مزید شامل کریں",
        "btn_clear_cart": "🗑 کارٹ خالی کریں",
        "btn_cart": "🛒 کارٹ · {count}",
        "btn_submit": "🚀 آرڈر بھیجیں",
        "btn_start_over": "↩️ دوبارہ شروع کریں",
        "btn_change_pid": "✏️ پلیئر آئی ڈی بدلیں",
        "btn_refresh": "🔄 تازہ کریں",
        "btn_get_help": "🆘 مدد لیں",
        "btn_forget_pids": "🗑 محفوظ آئی ڈیز ہٹائیں",
        "btn_qty": "تعداد {qty}",
        "home_welcome_back": "خوش آمدید، {name}",
        "home_welcome": "خوش آمدید، {name}",
        "home_stats": "{active} فعال · {due} ادائیگی کے منتظر",
        "home_last": "آخری آرڈر <code>{order_id}</code> · {status}",
        "home_empty": "اپنا پہلا آرڈر دینے کے لیے <b>نیا آرڈر</b> دبائیں۔",
        "signin_title": "🔐 <b>ممبر سائن اِن</b>",
        "signin_body": "یہ بوٹ صرف مدعو ممبرز کے لیے ہے۔ ایڈمن کی طرف سے ملنے والا ایکسس کوڈ بھیجیں۔",
        "signin_note": "بھیجنے کے بعد آپ کا کوڈ والا پیغام ڈیلیٹ کر دیا جاتا ہے۔",
        "signin_ok": "✅ <b>{name}، آپ سائن اِن ہو گئے!</b>",
        "code_failed": "❌ <b>یہ کوڈ کام نہیں کر رہا</b>",
        "code_invalid": "غلط ایکسس کوڈ۔ {remaining} کوششیں باقی ہیں۔",
        "code_locked": "بہت زیادہ غلط کوششیں۔ {minutes} منٹ بعد دوبارہ کوشش کریں۔",
        "access_revoked": "⛔ آپ کی رسائی ختم کر دی گئی ہے۔ ایڈمن سے رابطہ کریں۔",
        "account_locked": "🔒 بہت زیادہ غلط کوڈز۔ {minutes} منٹ بعد دوبارہ کوشش کریں۔",
        "signed_out": "🔓 <b>آپ سائن آؤٹ ہو گئے</b>\n\nدوبارہ سائن اِن کے لیے /start بھیجیں۔",
        "need_signin": "🔐 پہلے سائن اِن کے لیے /start بھیجیں۔",
        "slow_down": "⏳ ایک لمحہ — دوبارہ کوشش کریں۔",
        "maintenance": "🚧 <b>مینٹیننس</b>\n\nبوٹ عارضی طور پر بند ہے۔ تھوڑی دیر بعد دوبارہ آئیں۔",
        "lang_pick": "🌐 <b>اپنی زبان منتخب کریں</b>",
        "lang_set": "✅ زبان اردو کر دی گئی۔",
        "new_order": "نیا آرڈر",
        "step": "مرحلہ {step} از {total}",
        "choose_product": "پروڈکٹ منتخب کریں",
        "choose_product_hint": "پروڈکٹ پر ٹیپ کریں، یا تلاش کے لیے پیکج کا نام لکھیں۔",
        "choose_package": "کارٹ میں شامل کرنے کے لیے پیکج پر ٹیپ کریں۔",
        "choose_qty": "کتنے چاہئیں؟",
        "added_toast": "کارٹ میں شامل ✓",
        "no_products": "😕 ابھی کوئی پروڈکٹ دستیاب نہیں۔ بعد میں دیکھیں۔",
        "product_gone": "❌ یہ پروڈکٹ اب دستیاب نہیں۔",
        "search_results": "🔎 “{query}” کے نتائج",
        "search_none": "“{query}” سے کوئی پیکج نہیں ملا۔ کوئی اور لفظ آزمائیں۔",
        "cart_title": "🛒 <b>آپ کی کارٹ</b>",
        "cart_hint": "تعداد بدلیں، مزید شامل کریں یا آگے بڑھیں۔",
        "cart_empty": "🛒 آپ کی کارٹ خالی ہے۔",
        "cart_changed": "🛒 کارٹ بدل گئی ہے۔ دوبارہ دیکھیں۔",
        "qty_range": "1 سے 99 تک منتخب کریں۔",
        "qty_hint": "تعداد بدلنے کے لیے + / − استعمال کریں۔",
        "pid_title": "آپ کی پلیئر آئی ڈی",
        "pid_prompt": "اپنی پلیئر آئی ڈی بھیجیں۔ بھیجنے سے پہلے اچھی طرح چیک کر لیں۔",
        "pid_saved_hint": "یا حالیہ آئی ڈی پر ٹیپ کریں:",
        "pid_invalid": "❌ یہ پلیئر آئی ڈی درست نہیں لگتی۔ 3 سے 20 حروف، بغیر اسپیس کے۔",
        "pids_cleared": "محفوظ آئی ڈیز ہٹا دی گئیں۔",
        "session_expired": "⌛ آرڈر سیشن ختم ہو گیا۔ مینو سے نیا آرڈر شروع کریں۔",
        "review_title": "🧾 <b>اپنا آرڈر چیک کریں</b>",
        "review_hint": "سب کچھ چیک کریں، پھر بھیجیں۔",
        "player_id": "پلیئر آئی ڈی",
        "checking": "⏳ آرڈر چیک ہو رہا ہے…",
        "selection_changed": "⚠️ کچھ پیکجز اب دستیاب نہیں۔ دوبارہ شروع کریں۔",
        "no_supplier": "⚠️ ابھی یہ آرڈر نہیں لیا جا سکتا — ایک پیکج کا سپلائر سیٹ نہیں۔ ایڈمن کو بتا دیا گیا ہے؛ کچھ بھی نہیں بھیجا گیا۔",
        "limit_title": "🚫 <b>آرڈر کی حد پوری ہو گئی</b>",
        "limit_blocked": "آپ کے اکاؤنٹ کے لیے آرڈرنگ بند ہے۔",
        "limit_daily": "آپ آج کی حد ({limit} آرڈرز) پوری کر چکے ہیں۔ کل دوبارہ کوشش کریں۔",
        "create_failed": "⚠️ آرڈر نہیں بن سکا۔ تھوڑی دیر بعد دوبارہ کوشش کریں۔",
        "order_received": "⏳ آرڈر موصول — سپلائرز کو بھیجا جا رہا ہے…",
        "sent_title": "✅ آرڈر بھیج دیا گیا",
        "sent_hint": "مکمل ہونے پر ہم آپ کو یہیں اطلاع دیں گے۔",
        "retry_title": "⏳ آرڈر محفوظ — دوبارہ بھیجا جا رہا ہے",
        "retry_hint": "ہم خودکار طور پر کوشش کرتے رہیں گے۔ براہ کرم دوبارہ آرڈر نہ دیں۔",
        "attention_title": "⚠️ آرڈر محفوظ — توجہ درکار",
        "attention_hint": "ایڈمن کو اطلاع دے دی گئی ہے۔ براہ کرم دوبارہ آرڈر نہ دیں۔",
        "reorder_missing": "دستیاب نہیں، نکال دیے گئے: {names}",
        "reorder_none": "😕 یہ پیکجز اب دستیاب نہیں۔",
        "order_not_found": "⚠️ یہ آرڈر نہیں ملا۔",
        "order_cancelled_flow": "✖ آرڈر منسوخ۔ کارٹ خالی کر دی گئی۔",
        "menu_cart_cleared": "آپ کی ادھوری کارٹ خالی کر دی گئی۔",
        "orders_title": "📦 <b>آپ کے آرڈرز</b>",
        "orders_page": "صفحہ {page} از {pages}",
        "orders_empty": "📭 ابھی کوئی آرڈر نہیں۔ مینو سے پہلا آرڈر دیں۔",
        "more_items": "+{count} مزید",
        "order": "آرڈر",
        "items": "آئٹمز",
        "st_pending": "سپلائر کا انتظار",
        "st_processing": "جزوی مکمل",
        "st_completed": "مکمل",
        "st_failed": "مدد درکار",
        "st_cancelled": "منسوخ",
        "tl_placed": "آرڈر دیا گیا",
        "tl_sent": "سپلائر کو بھیجا گیا",
        "tl_retrying": "دوبارہ بھیجا جا رہا ہے",
        "tl_working": "سپلائر کام کر رہا ہے",
        "tl_delivered": "ڈیلیور ہو گیا",
        "tl_failed": "مدد درکار",
        "tl_cancelled": "منسوخ",
        "proof_received": "📸 ڈیلیوری کا ثبوت موصول",
        "n_completed": "🎉 <b>آپ کا آرڈر مکمل ہو گیا</b>",
        "n_failed": "❌ <b>آپ کے آرڈر کو مدد درکار ہے</b>\nسپلائر اسے مکمل نہیں کر سکا۔ ہماری ٹیم کو اطلاع دے دی گئی ہے۔",
        "n_cancelled": "🚫 <b>آپ کا آرڈر منسوخ ہو گیا</b>",
        "n_auto_cancel": "⏱️ <b>آرڈر خودکار طور پر منسوخ</b>\nسپلائر نے {minutes} منٹ میں جواب نہیں دیا۔ آپ کسی بھی وقت دوبارہ آرڈر دے سکتے ہیں۔",
        "n_partial_timeout": "⚠️ <b>آپ کے آرڈر کا کچھ حصہ وقت پر مکمل نہیں ہوا</b>\nمکمل: {done}۔ باقی کے لیے ٹیم کو اطلاع دے دی گئی ہے۔",
        "n_group_timeout": "⚠️ <b>ایک سپلائر گروپ نے وقت پر جواب نہیں دیا</b>\nآپ کے آرڈر کا باقی حصہ جاری ہے۔",
        "n_group_issue": "⚠️ <b>ایک سپلائر گروپ نے مسئلہ بتایا</b>\nآرڈر کا باقی حصہ جاری ہے، اور ٹیم کو اطلاع دے دی گئی ہے۔",
        "n_proof": "📸 آرڈر <code>{order_id}</code> کا <b>ڈیلیوری ثبوت</b>",
        "balance_title": "💰 <b>آپ کا بیلنس</b>",
        "balance_due": "ادائیگی کے منتظر مکمل آرڈرز: <b>{count}</b>",
        "balance_last": "آخری ادائیگی: {date}",
        "never": "کبھی نہیں",
        "balance_hint": "ادائیگیوں کی تصدیق ایڈمن کرتا ہے۔",
        "support_title": "🆘 <b>مدد سے رابطہ</b>",
        "support_about": "آرڈر <code>{order_id}</code> کے بارے میں",
        "support_prompt": "اپنا پیغام ایک ہی میسج میں بھیجیں۔ ایڈمن یہیں جواب دے گا۔",
        "support_too_long": "براہ کرم {limit} حروف سے کم رکھیں۔",
        "support_sent": "✅ <b>پیغام بھیج دیا گیا۔</b> ایڈمن جلد یہیں جواب دے گا۔",
        "support_failed": "⚠️ ابھی مدد سے رابطہ نہیں ہو سکا۔ بعد میں کوشش کریں۔",
        "support_cancelled": "✖ مدد کی درخواست منسوخ۔",
        "support_reply": "💬 <b>مدد کا جواب</b>",
        "help": (
            "💬 <b>رہنمائی</b>\n\n"
            "<b>آرڈر کیسے دیں</b>\n"
            "1. <b>نیا آرڈر</b> دبائیں اور پروڈکٹ منتخب کریں۔\n"
            "2. پیکج اور تعداد منتخب کریں۔\n"
            "3. اپنی پلیئر آئی ڈی بھیجیں (یا حالیہ آئی ڈی پر ٹیپ کریں)۔\n"
            "4. چیک کریں اور بھیج دیں۔\n\n"
            "<b>آرڈر کی صورتحال</b>\n"
            "⏳ سپلائر کا انتظار · ⚙️ جزوی مکمل · ✅ مکمل\n"
            "❌ مدد درکار · 🚫 منسوخ\n\n"
            "مکمل آرڈرز پر <b>دوبارہ آرڈر</b> کا بٹن ہوتا ہے۔ سوال ہو تو <b>مدد</b> دبائیں۔\n\n"
            "/start مینو · /myorders آرڈرز · /support مدد · /language زبان"
        ),
        "about": (
            "🏪 <b>{store} کے بارے میں</b>\n\n"
            "یہیں ٹیلیگرام میں آرڈر دیں اور ان کی صورتحال لائیو دیکھیں۔\n"
            "ہر آرڈر سیدھا اس پروڈکٹ کے سپلائر کو جاتا ہے، اور مکمل ہوتے ہی آپ کو پیغام ملتا ہے۔"
        ),
        "bot_description": "ٹاپ اپ آرڈر کریں اور لائیو ٹریک کریں۔ صرف مدعو ممبرز کے لیے — ایکسس کوڈ کے لیے ایڈمن سے رابطہ کریں۔",
        "bot_short_description": "ٹاپ اپ آرڈر کریں اور لائیو ٹریک کریں۔",
        "cmd_start": "مینو کھولیں",
        "cmd_myorders": "آپ کے آرڈرز",
        "cmd_support": "مدد سے رابطہ",
        "cmd_language": "زبان بدلیں",
        "cmd_help": "طریقہ کار",
        "cmd_cancel": "موجودہ مرحلہ منسوخ کریں",
        "cmd_logout": "سائن آؤٹ",
    },
}


def normalize(language: str | None) -> str:
    return language if language in TEXTS else DEFAULT_LANGUAGE


def guess_language(telegram_language_code: str | None) -> str:
    """Pick a starting language from the Telegram app setting."""
    return "ur" if (telegram_language_code or "").lower().startswith("ur") else DEFAULT_LANGUAGE


def t(language: str | None, key: str, **params) -> str:
    """Translate a key. Params are HTML-escaped unless passed as markup via raw_* names."""
    text = TEXTS[normalize(language)].get(key) or TEXTS[DEFAULT_LANGUAGE][key]
    if not params:
        return text
    safe = {
        name.removeprefix("raw_"): value if name.startswith("raw_") else html.escape(str(value), quote=False)
        for name, value in params.items()
    }
    return text.format(**safe)
