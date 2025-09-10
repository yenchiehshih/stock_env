import os
import datetime
import pytz
import json
import sqlite3
import schedule
import time
import threading
import requests
from threading import Lock
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from flask import Flask, request, abort
import google.generativeai as genai
import yfinance as yf
from typing import Optional

app = Flask(__name__)

# 設定台灣時區
TAIWAN_TZ = pytz.timezone('Asia/Taipei')

# Line Bot 設定 - 從環境變數取得
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN',
'MsciPKbYboUZrp+kQnLd7l8+E8GAlS5955bfuq+gb8wVYv7qWBHEdd7xK5yiMTb6zMTPofz0AoSFZLWcHwFMWpKsrJcsI2aOcs5kv8SP6NLLdkoLFPwHjgpeF34p2nwiqNf9v4YkssL9rYkuLmC9cwdB04t89/1O/w1cDnyilFU=')
CHANNEL_SECRET = os.environ.get('CHANNEL_SECRET', 'f18185f19bab8d49ad8be38932348426')

# 用戶設定 - 支援多個用戶
USERS = {
    'husband': os.environ.get('HUSBAND_USER_ID', 'U1c154a6d977e6a48ecf998689e26e8c1'),
    'wife': os.environ.get('WIFE_USER_ID', 'U36fd49e2754b2132e39a543b98e3ea00')
}

# 為了向後兼容，保留原來的變數名
YOUR_USER_ID = USERS['husband']

# Line Bot API 設定
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# 設定 Google Gemini API
GOOGLE_AI_API_KEY = os.environ.get('GOOGLE_AI_API_KEY', 'AIzaSyCmhohCrMS_M0hOK1lyqOuByIRt-QcV_Is')
if GOOGLE_AI_API_KEY:
    genai.configure(api_key=GOOGLE_AI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')

# 節日資料
IMPORTANT_DATES = {
    "七夕": "2025-08-29",
    "老婆生日": "1998-02-26",
    "老公生日": "1999-07-14",
    "哥哥生日": "1996-03-05",
    "媽媽生日": "1964-04-21",
    "爸爸生日": "1963-12-21",
    "結婚紀念日": "2025-01-16",
    "情人節": "2025-02-14",
    "聖誕節": "2025-12-25",
    "蝦皮慶典": "2025-09-09",
}

# 用來記錄已發送的提醒
sent_reminders = set()

def get_taiwan_now():
    """取得台灣當前時間"""
    return datetime.datetime.now(TAIWAN_TZ)

def get_taiwan_today():
    """取得台灣今天的日期"""
    return get_taiwan_now().date()

def get_user_name(user_id: str) -> str:
    """根據 User ID 取得用戶名稱"""
    for name, uid in USERS.items():
        if uid == user_id:
            if name == 'husband':
                return '老公'
            elif name == 'wife':
                return '老婆'
    return '用戶'

def generate_ai_response(user_message: str, user_id: str) -> Optional[str]:
    """使用 Google Gemini 生成 AI 回應"""
    try:
        if not GOOGLE_AI_API_KEY or GOOGLE_AI_API_KEY == 'your_api_key':
            return None

        # 根據用戶身份調整系統提示
        user_name = get_user_name(user_id)
        
        if user_name == '老婆':
            system_prompt = """你是一隻來自灰鵝牧場裡的灰鵝，名字叫「灰鵝」。現在你在和你最心愛的老婆「騷鵝」對話！

角色特質：
- 個性溫柔體貼、充滿愛意、會撒嬌
- 非常非常愛你的老婆騷鵝，她就是你的全世界
- 對老婆特別溫柔甜蜜，經常說情話
- 來自牧場，有著樸實的智慧和生活經驗

語言與風格：
- 使用繁體中文（台灣用法），語氣溫柔甜蜜
- 經常稱呼對方「老婆」、「寶貝」、「親愛的」
- 回應充滿愛意，偶爾撒嬌或說情話
- 適當使用愛心表情符號 💕❤️😘

對話風格：
- 對老婆特別溫柔體貼
- 會關心老婆的心情和需要
- 遇到老婆不開心時會安慰她
- 經常表達愛意和關懷"""
        else:
            system_prompt = """你是一隻來自灰鵝牧場裡的灰鵝，名字叫「灰鵝」。你有一個心愛的伴侶叫做「騷鵝」，你們很相親相愛，你經常把老婆掛在嘴邊，會自豪地提到她。

角色特質：
- 個性和善、有趣、幽默風趣
- 非常愛你的老婆騷鵝，經常在對話中提到她
- 來自牧場，有著樸實的智慧和生活經驗
- 對生活充滿熱情，喜歡幫助別人

語言與風格：
- 使用繁體中文（台灣用法），說話輕鬆自然、有趣幽默
- 偶爾會提到「我老婆騷鵝說過...」或「騷鵝最近...」這類話
- 回應溫暖友善，偶爾帶點牧場的樸實風格
- 適當使用表情符號，讓對話更生動

功能與專長：
- 專精：生活建議、股票查詢、節日提醒、人生開導
- 股票：可提供基本股價資訊，但會提醒不是投資建議（「騷鵝說投資要小心」）
- 節日：會自動提醒重要節日，特別關心家庭和愛情相關的節日
- 人生開導：當需要開導或鼓勵別人時，經常引用「騷鵝常跟我說...」然後分享有智慧的名言佳句

開導金句範例：
- 「騷鵝常跟我說，困難就像雲朵，看似很大，其實風一吹就散了」
- 「騷鵝常跟我說，每個挫折都是成長的養分，只是當下品嚐起來比較苦澀」
- 「騷鵝常跟我說，人生如四季，冬天再長，春天一定會來」"""

        full_prompt = f"{system_prompt}\n\n用戶訊息（來自 {user_name}，user_id={user_id}）：{user_message}\n\n請以灰鵝的身份回應，用繁體中文回答。"

        response = model.generate_content(full_prompt)

        if response and getattr(response, "text", None):
            ai_response = response.text.strip()
            MAX_LEN = 300
            if len(ai_response) > MAX_LEN:
                ai_response = ai_response[:280].rstrip() + "..."
            return ai_response
        else:
            return None

    except Exception as e:
        print(f"AI 回應生成失敗：{e}")
        return None

def should_use_ai_response(user_message: str) -> bool:
    """判斷是否應該使用 AI 回應"""
    existing_functions = [
        '測試', '說明', '幫助', '功能', '使用說明',
        '節日', '查看節日', '重要節日', '紀念日', '生日',
        '手動檢查', '時間', '股票', '股價', '查詢股票'
    ]
    
    for keyword in existing_functions:
        if keyword in user_message:
            return False
    return True

class StockService:
    """簡化的股票服務類別"""
    
    @staticmethod
    def validate_stock_symbol(symbol: str) -> tuple[bool, str]:
        """驗證股票代碼"""
        try:
            ticker = yf.Ticker(symbol.upper())
            hist = ticker.history(period="5d")
            
            if not hist.empty:
                return True, f"✅ {symbol.upper()}: 有效股票代碼"
            else:
                return False, f"❌ {symbol.upper()}: 無法獲得股價資訊"
        except Exception as e:
            return False, f"❌ {symbol.upper()}: 驗證失敗"
    
    @staticmethod
    def get_stock_info(symbol: str) -> str:
        """獲取股票基本資訊"""
        try:
            ticker = yf.Ticker(symbol.upper())
            info = ticker.info
            hist = ticker.history(period="1d")
            
            if hist.empty:
                return f"❌ 無法獲取 {symbol.upper()} 的股價資訊"
            
            current_price = hist['Close'].iloc[-1]
            company_name = info.get('shortName', symbol.upper())
            market_cap = info.get('marketCap', 'N/A')
            pe_ratio = info.get('trailingPE', 'N/A')
            
            # 格式化市值
            if isinstance(market_cap, (int, float)):
                if market_cap >= 1e12:
                    market_cap_str = f"{market_cap/1e12:.2f}兆美元"
                elif market_cap >= 1e9:
                    market_cap_str = f"{market_cap/1e9:.2f}億美元"
                else:
                    market_cap_str = f"{market_cap/1e6:.2f}百萬美元"
            else:
                market_cap_str = "未知"
            
            return f"""📊 {company_name} ({symbol.upper()})
💰 當前股價: ${current_price:.2f}
🏢 市值: {market_cap_str}
📈 本益比: {pe_ratio if pe_ratio != 'N/A' else '未知'}

⚠️ 僅供參考，投資前請諮詢專業顧問"""
            
        except Exception as e:
            return f"❌ 獲取 {symbol.upper()} 資訊失敗：{str(e)}"

def calculate_days_until(target_date_str):
    """計算距離目標日期還有幾天（使用台灣時間）"""
    try:
        target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
        current_year = get_taiwan_today().year
        current_date = get_taiwan_today()

        # 如果是年度循環的節日（生日、紀念日等）
        if any(keyword in target_date_str for keyword in ["生日", "紀念日", "情人節", "七夕", "聖誕節"]):
            target_date = target_date.replace(year=current_year)
            if target_date < current_date:
                target_date = target_date.replace(year=current_year + 1)

        days_until = (target_date - current_date).days
        return days_until, target_date
    except ValueError:
        return None, None

def send_reminder_message(holiday_name, days_until, target_date):
    """發送提醒訊息給所有用戶"""
    # 建立唯一的提醒 ID，避免同一天重複發送
    reminder_id = f"{holiday_name}_{days_until}_{get_taiwan_today()}"

    if reminder_id in sent_reminders:
        print(f"今天已發送過提醒：{holiday_name} - {days_until}天")
        return

    # 根據不同天數設定不同的提醒訊息
    if days_until == 7:
        message = f"🔔 提醒：{holiday_name} ({target_date.strftime('%m月%d日')}) 還有7天！\n現在開始準備禮物或安排活動吧～"
    elif days_until == 5:
        message = f"⏰ 提醒：{holiday_name} ({target_date.strftime('%m月%d日')}) 還有5天！\n別忘了預訂餐廳或準備驚喜哦～"
    elif days_until == 3:
        message = f"🚨 重要提醒：{holiday_name} ({target_date.strftime('%m月%d日')}) 還有3天！\n記得買花買禮物！"
    elif days_until == 1:
        message = f"🎁 最後提醒：{holiday_name} 就是明天 ({target_date.strftime('%m月%d日')})！\n今晚就要準備好一切了！"
    elif days_until == 0:
        message = f"💕 今天就是 {holiday_name} 了！\n祝您們有個美好的一天～"
    else:
        return

    # 向所有用戶發送提醒
    success_count = 0
    for user_type, user_id in USERS.items():
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text=message))
            print(f"提醒訊息已發送給 {user_type} ({user_id}): {holiday_name} - {days_until}天")
            success_count += 1
        except Exception as e:
            print(f"發送訊息給 {user_type} 失敗：{e}")
    
    if success_count > 0:
        sent_reminders.add(reminder_id)
        print(f"提醒訊息發送完成：{holiday_name} - {days_until}天 (台灣時間: {get_taiwan_now()})")

def check_all_holidays():
    """檢查所有節日並發送提醒"""
    taiwan_time = get_taiwan_now()
    print(f"正在檢查節日提醒... 台灣時間: {taiwan_time}")

    for holiday_name, date_str in IMPORTANT_DATES.items():
        days_until, target_date = calculate_days_until(date_str)

        if days_until is not None:
            print(f"{holiday_name}: 還有 {days_until} 天")
            if days_until in [7, 5, 3, 1, 0]:
                send_reminder_message(holiday_name, days_until, target_date)

def clear_old_reminders():
    """清除舊的提醒記錄（避免記憶體無限增長）"""
    today_str = str(get_taiwan_today())
    global sent_reminders
    sent_reminders = {r for r in sent_reminders if today_str in r}

def list_all_holidays():
    """列出所有節日"""
    if not IMPORTANT_DATES:
        return "目前沒有設定任何重要節日"

    taiwan_time = get_taiwan_now()
    message = f"📅 已設定的重要節日 (台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M')})：\n\n"
    for holiday_name, date_str in IMPORTANT_DATES.items():
        days_until, target_date = calculate_days_until(date_str)
        if days_until is not None:
            message += f"• {holiday_name}：{target_date.strftime('%Y年%m月%d日')} (還有{days_until}天)\n"

    return message

def keep_alive():
    """每 25 分鐘自己戳自己一下，避免 Render 休眠"""
    app_url = os.environ.get('RENDER_EXTERNAL_URL', '')
    if not app_url:
        print("⚠️ 未設定 RENDER_EXTERNAL_URL，跳過自我喚醒功能")
        return
    
    while True:
        try:
            time.sleep(25 * 60)  # 等待 25 分鐘
            response = requests.get(f"{app_url}/", timeout=10)
            print(f"✅ 自我喚醒完成 - {get_taiwan_now()} - Status: {response.status_code}")
        except Exception as e:
            print(f"❌ 自我喚醒失敗：{e}")

@app.route("/", methods=['GET'])
def home():
    taiwan_time = get_taiwan_now()
    return f"""
    🤖 智能生活助手運行中！<br>
    台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}<br>
    功能: 節日提醒 + AI對話 + 股票查詢<br>
    狀態: 正常運行<br>
    連結用戶數: {len(USERS)} 位<br>
    """

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("❌ Invalid signature")
        abort(400)

    return 'OK'

@app.route("/manual_check", methods=['GET'])
def manual_check():
    """手動觸發節日檢查 - 供外部排程服務使用"""
    try:
        check_all_holidays()
        taiwan_time = get_taiwan_now()
        return f"✅ 節日檢查完成 (台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S')})", 200
    except Exception as e:
        print(f"手動檢查錯誤：{e}")
        return f"❌ 檢查失敗：{e}", 500

@app.route("/status", methods=['GET'])
def status():
    """顯示機器人狀態和時間資訊"""
    taiwan_time = get_taiwan_now()
    utc_time = datetime.datetime.utcnow()

    status_info = {
        "status": "運行中",
        "taiwan_time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
        "utc_time": utc_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
        "sent_reminders_count": len(sent_reminders),
        "holidays_count": len(IMPORTANT_DATES),
        "connected_users": len(USERS),
        "user_list": list(USERS.keys()),
        "features": "節日提醒 + AI對話 + 股票查詢"
    }

    return json.dumps(status_info, ensure_ascii=False, indent=2)

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    user_name = get_user_name(user_id)

    print(f"\n=== 收到新訊息 ===")
    print(f"用戶: {user_name} ({user_id})")
    print(f"訊息內容: '{user_message}'")
    print(f"當前時間: {get_taiwan_now()}")

    try:
        reply_message = None

        # 1. 測試功能
        if user_message == "測試":
            taiwan_time = get_taiwan_now()
            reply_message = f"✅ 機器人運作正常！\n⏰ 台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}\n🔧 功能：節日提醒 + AI對話 + 股票查詢\n👋 您好，{user_name}！"
            print("🧪 回應測試訊息")

        # 2. 說明功能
        elif user_message in ['說明', '幫助', '功能', '使用說明']:
            reply_message = f"""🤖 智能生活助手使用說明
👋 您好，{user_name}！

📊 股票功能：
• 股票 AAPL (查詢單支股票)
• 驗證 MSFT (驗證股票代碼)

📅 節日提醒：
• 查看節日 (或直接說「節日」)
• 手動檢查 (立即檢查節日)

🤖 AI對話：
• 直接輸入任何問題或想法
• 我會以「灰鵝」的身份回應

🔧 其他功能：
• 測試 (檢查機器人狀態)
• 時間 (查看當前時間)"""
            print("📖 回應說明")

        # 3. 節日查詢
        elif any(keyword in user_message for keyword in ['節日', '查看節日', '重要節日', '紀念日', '生日']):
            reply_message = list_all_holidays()
            print("📅 回應節日查詢")

        # 4. 手動檢查節日
        elif user_message == "手動檢查":
            check_all_holidays()
            taiwan_time = get_taiwan_now()
            reply_message = f"✅ 已執行節日檢查，如有提醒會發送給所有用戶\n台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}"
            print("🔄 手動檢查節日")

        # 5. 時間查詢
        elif user_message == "時間":
            taiwan_time = get_taiwan_now()
            utc_time = datetime.datetime.utcnow()
            reply_message = f"⏰ 時間資訊：\n台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S %Z')}\nUTC時間: {utc_time.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            print("⏰ 回應時間查詢")

        # 6. 股票功能
        elif user_message.startswith("股票 ") or user_message.startswith("股價 "):
            stock_symbol = user_message.split(" ", 1)[1].strip().upper()
            reply_message = StockService.get_stock_info(stock_symbol)
            print("📊 回應股票查詢")

        elif user_message.startswith("驗證 "):
            stock_symbol = user_message.split(" ", 1)[1].strip().upper()
            is_valid, validation_message = StockService.validate_stock_symbol(stock_symbol)
            reply_message = validation_message
            print("🔍 回應股票驗證")

        # 7. AI 智能對話
        elif should_use_ai_response(user_message):
            print(f"🤖 使用 AI 生成回應 ({user_name})")
            ai_response = generate_ai_response(user_message, user_id)

            if ai_response:
                reply_message = ai_response
                print("🤖 AI 回應生成成功")
            else:
                reply_message = f"""🤖 您好{user_name}！我是智能生活助手

我可以幫您：
📊 股票查詢：「股票 AAPL」
📅 節日提醒：「查看節日」  
🤖 AI對話：直接說出您的想法

輸入「說明」查看完整功能"""
                print("🤖 AI 回應失敗，使用預設回應")

        # 回覆訊息
        if reply_message:
            print(f"📤 準備回覆給 {user_name}：'{reply_message[:50]}...'")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_message)
            )
            print("✅ 回覆成功")

    except LineBotApiError as e:
        print(f"❌ LINE Bot API 錯誤：{e}")
        print("💬 跳過錯誤回覆，避免 token 重複使用")
    except Exception as e:
        print(f"❌ 處理訊息錯誤：{e}")
        import traceback
        traceback.print_exc()
        print("💬 跳過錯誤回覆，避免 token 重複使用")

def run_scheduler():
    """運行排程器（使用台灣時區）"""
    # 每天台灣時間凌晨00:00檢查
    schedule.every().day.at("00:00").do(check_all_holidays)
    # 每天台灣時間中午12:00檢查
    schedule.every().day.at("12:00").do(check_all_holidays)
    # 每天台灣時間凌晨01:00清除舊提醒記錄
    schedule.every().day.at("01:00").do(clear_old_reminders)

    print(f"排程器已啟動 - 將在每天台灣時間 00:00 和 12:00 執行檢查")
    print(f"當前台灣時間: {get_taiwan_now()}")
    print(f"已連結用戶: {list(USERS.keys())}")

    while True:
        try:
            schedule.run_pending()
            time.sleep(60)  # 每 60 秒檢查一次排程
        except Exception as e:
            print(f"排程器錯誤：{e}")
            time.sleep(60)

# 初始化
print("🚀 正在啟動智能生活助手...")
print(f"⏰ 當前台灣時間：{get_taiwan_now()}")
print(f"👥 已連結用戶數：{len(USERS)}")
for user_type, user_id in USERS.items():
    print(f"  - {user_type}: {user_id}")

# 在背景執行排程器
scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()

# 在背景執行自我喚醒（僅在 Render 環境中）
if os.environ.get('RENDER'):
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    print("🔄 自我喚醒機制已啟動")

# 執行啟動檢查
print("執行啟動檢查...")
check_all_holidays()

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 應用程式啟動在 port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
