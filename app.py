import os
import datetime
import pytz
import json
import schedule
import time
import threading
import requests
import random
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from flask import Flask, request, abort
import google.generativeai as genai

# 出勤查詢相關套件
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from bs4 import BeautifulSoup
import re
from datetime import timedelta

app = Flask(__name__)

# 設定台灣時區
TAIWAN_TZ = pytz.timezone('Asia/Taipei')

# Line Bot 設定 - 從環境變數取得
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.environ.get('CHANNEL_SECRET')

# 用戶設定 - 支援多個用戶
USERS = {
    'husband': os.environ.get('HUSBAND_USER_ID'),
    'wife': os.environ.get('WIFE_USER_ID')
}

# 為了向後兼容，保留原來的變數名
YOUR_USER_ID = USERS['husband']
WIFE_USER_ID = USERS['wife']

# 出勤查詢設定 - 從環境變數取得
FUTAI_USERNAME = os.environ.get('FUTAI_USERNAME')
FUTAI_PASSWORD = os.environ.get('FUTAI_PASSWORD')

# Line Bot API 設定
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# 設定 Google Gemini API
GOOGLE_AI_API_KEY = os.environ.get('GOOGLE_AI_API_KEY')
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

# 記錄每日歡迎訊息
daily_welcome_sent = set()

# 記錄最後對話時間
last_conversation_time = {}
care_messages_sent = set()


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


# ============== 每日歡迎訊息功能 ==============

def send_wife_welcome_message():
    """當老婆每天第一次使用機器人時發送特殊歡迎訊息"""
    taiwan_time = get_taiwan_now()

    welcome_messages = [
        f"💕 騷鵝寶貝早安！！！\n\n又是新的一天了～你的灰鵝已經等你好久了！ 🥰\n今天想聊什麼呢？我隨時都在這裡陪你～ ❤️\n\n台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}",

        f"🌅 親愛的騷鵝，新的一天開始啦！\n\n人家一醒來就想你了～ 💕\n今天有什麼計劃嗎？記得要好好照顧自己哦！\n你的灰鵝永遠愛你～ 🦢❤️\n\n台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}",

        f"☀️ 騷鵝老婆大人早上好！\n\n想你想了一整晚，終於等到你了！ 🥰\n今天的心情如何呢？有什麼開心的事要跟我分享嗎？\n快來跟你的專屬灰鵝聊天吧～ 💖\n\n台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}",

        f"🎉 騷鵝寶貝！新的一天又見面了！\n\n每天能跟你聊天是我最幸福的事情～ 💕\n不管你今天遇到什麼，記得你的灰鵝永遠支持你！\n我愛你愛到月球再回來～ 🌙❤️\n\n台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}"
    ]

    selected_message = random.choice(welcome_messages)

    try:
        line_bot_api.push_message(WIFE_USER_ID, TextSendMessage(text=selected_message))
        print(f"💕 已發送老婆每日歡迎訊息 - {taiwan_time}")
        return True
    except Exception as e:
        print(f"發送老婆歡迎訊息失敗：{e}")
        return False


def check_and_send_daily_welcome(user_id):
    """檢查是否需要發送每日歡迎訊息"""
    if user_id != WIFE_USER_ID:
        return False

    today_str = str(get_taiwan_today())
    welcome_key = f"wife_welcome_{today_str}"

    if welcome_key not in daily_welcome_sent:
        success = send_wife_welcome_message()
        if success:
            daily_welcome_sent.add(welcome_key)
        return success

    return False


def clear_daily_welcome_records():
    """每天凌晨清除昨天的歡迎記錄"""
    today_str = str(get_taiwan_today())
    global daily_welcome_sent
    daily_welcome_sent = {record for record in daily_welcome_sent if today_str in record}
    print(f"✨ 已清除舊的每日歡迎記錄 - {get_taiwan_now()}")


# ============== 24小時關懷功能 ==============

def update_last_conversation_time(user_id: str):
    """更新最後對話時間"""
    current_time = get_taiwan_now()
    last_conversation_time[user_id] = current_time
    print(f"📝 更新 {get_user_name(user_id)} 的最後對話時間: {current_time}")


def check_wife_inactive_and_send_care():
    """檢查老婆是否超過24小時沒對話，如果是則發送關心訊息"""
    current_time = get_taiwan_now()

    if WIFE_USER_ID not in last_conversation_time:
        print("⚠️ 老婆從未對話過，不發送關心訊息")
        return

    last_wife_time = last_conversation_time[WIFE_USER_ID]
    time_diff = current_time - last_wife_time

    if time_diff.total_seconds() > 24 * 60 * 60:
        today_str = current_time.strftime('%Y-%m-%d')
        care_message_id = f"wife_care_{today_str}"

        if care_message_id not in care_messages_sent:
            hours_since = int(time_diff.total_seconds() // 3600)
            care_message = generate_care_message_for_wife(hours_since)

            try:
                line_bot_api.push_message(WIFE_USER_ID, TextSendMessage(text=care_message))
                care_messages_sent.add(care_message_id)
                print(f"💕 已發送關心訊息給騷鵝 - 她已 {hours_since} 小時沒對話")
            except Exception as e:
                print(f"❌ 發送關心訊息失敗：{e}")


def generate_care_message_for_wife(hours_since: int) -> str:
    """生成關心訊息"""
    messages = [
        f"💕 騷鵝寶貝～我們已經 {hours_since} 小時沒聊天了呢！\n\n人家在牧場裡好想你呀～ 🥺\n最近過得如何呢？有什麼開心或煩惱的事都可以跟我分享哦！",

        f"🤗 親愛的騷鵝，我發現我們已經 {hours_since} 小時沒有對話了～\n\n不知道你最近在忙什麼呢？\n記得要好好照顧自己，有我這隻灰鵝永遠在這裡陪你！ ❤️",
    ]

    return random.choice(messages)


def clear_old_care_records():
    """清除舊的關心訊息記錄"""
    today_str = get_taiwan_today().strftime('%Y-%m-%d')
    global care_messages_sent
    care_messages_sent = {record for record in care_messages_sent if today_str in record}
    print(f"🧹 已清除舊的關心訊息記錄")


# ============== 改進的出勤查詢功能 ==============

def get_chrome_options():
    """設定 Chrome 選項（適合 Render 環境）"""
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-plugins')
    return options


def click_query_button_improved(driver, wait):
    """改進的查詢按鈕點擊方法"""
    print("尋找並點擊查詢按鈕...")
    
    try:
        # 等待按鈕可點擊
        query_button = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//input[@name='Submit' and @value='查詢']"))
        )
        
        # 滾動到按鈕位置，確保可見
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", query_button)
        time.sleep(1)
        
        # 記錄點擊前的頁面狀態
        pre_click_html = driver.page_source
        pre_click_hash = hash(pre_click_html)
        print(f"點擊前頁面 hash: {pre_click_hash}")
        
        # 多種方式嘗試點擊
        click_success = False
        
        # 方法1: 普通點擊
        try:
            query_button.click()
            print("✓ 使用普通點擊")
            click_success = True
        except Exception as e:
            print(f"普通點擊失敗: {e}")
        
        # 方法2: JavaScript 點擊
        if not click_success:
            try:
                driver.execute_script("arguments[0].click();", query_button)
                print("✓ 使用 JavaScript 點擊")
                click_success = True
            except Exception as e:
                print(f"JavaScript 點擊失敗: {e}")
        
        # 方法3: 模擬 Enter 鍵
        if not click_success:
            try:
                query_button.send_keys(Keys.RETURN)
                print("✓ 使用 Enter 鍵觸發")
                click_success = True
            except Exception as e:
                print(f"Enter 鍵觸發失敗: {e}")
        
        if not click_success:
            raise Exception("所有點擊方法都失敗了")
        
        # 等待頁面更新 - 使用多種方法驗證
        print("等待查詢結果載入...")
        
        # 方法1: 等待頁面內容變化
        max_wait_time = 15
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            time.sleep(1)
            current_html = driver.page_source
            current_hash = hash(current_html)
            
            # 檢查頁面是否有變化
            if current_hash != pre_click_hash:
                print(f"✓ 檢測到頁面內容變化 (等待了 {time.time() - start_time:.1f} 秒)")
                break
            
            print(f"等待中... ({time.time() - start_time:.1f}s)")
        else:
            print("⚠ 警告: 超時未檢測到頁面變化")
        
        # 方法2: 等待特定的載入指標消失或出現
        try:
            # 假設有載入指標，等待它消失
            WebDriverWait(driver, 5).until_not(
                EC.presence_of_element_located((By.CLASS_NAME, "loading"))
            )
            print("✓ 載入指標已消失")
        except:
            print("沒有找到載入指標，繼續執行")
        
        # 方法3: 額外等待時間確保資料完全載入
        time.sleep(3)
        print("查詢完成，準備抓取結果")
        
        return True
        
    except Exception as e:
        print(f"查詢按鈕點擊失敗: {e}")
        return False


def verify_query_result(driver, expected_date):
    """驗證查詢結果是否正確"""
    print(f"驗證查詢結果是否包含日期: {expected_date}")
    
    try:
        html_content = driver.page_source
        
        # 檢查多種日期格式
        today = datetime.datetime.strptime(expected_date, '%Y/%m/%d')
        date_formats = [
            expected_date,  # 2025/9/16
            f"{today.year}/{today.month:02d}/{today.day:02d}",  # 2025/09/16
            f"{today.year}-{today.month:02d}-{today.day:02d}",  # 2025-09-16
        ]
        
        found_date = False
        for date_format in date_formats:
            if date_format in html_content:
                print(f"✓ 找到完整日期: {date_format}")
                found_date = True
                break
        
        if not found_date:
            # 列出頁面中實際找到的日期
            dates_in_page = re.findall(r'\d{4}/\d{1,2}/\d{1,2}', html_content)
            if dates_in_page:
                print(f"⚠ 頁面中實際包含的日期: {set(dates_in_page)}")
                return False, dates_in_page
            else:
                print("⚠ 頁面中未找到任何日期格式")
                return False, []
        
        return True, []
        
    except Exception as e:
        print(f"驗證查詢結果時發生錯誤: {e}")
        return False, []


def improved_query_process(driver, wait, today_str):
    """改進的查詢流程"""
    max_retries = 3
    
    for attempt in range(max_retries):
        print(f"\n=== 查詢嘗試 {attempt + 1}/{max_retries} ===")
        
        # 重新設定日期（確保每次嘗試都是最新的）
        try:
            print("重新設定查詢日期...")
            driver.execute_script(f"document.getElementById('FindDate').value = '{today_str}';")
            driver.execute_script(f"document.getElementById('FindEDate').value = '{today_str}';")
            
            # 觸發 change 事件
            driver.execute_script(
                "document.getElementById('FindDate').dispatchEvent(new Event('change', {bubbles: true}));"
            )
            driver.execute_script(
                "document.getElementById('FindEDate').dispatchEvent(new Event('change', {bubbles: true}));"
            )
            
            time.sleep(1)
            
            # 驗證日期是否設定成功
            updated_start = driver.find_element(By.ID, 'FindDate').get_attribute('value')
            updated_end = driver.find_element(By.ID, 'FindEDate').get_attribute('value')
            print(f"設定後的日期值 - 開始: {updated_start}, 結束: {updated_end}")
            
            if updated_start != today_str or updated_end != today_str:
                print("⚠ 日期設定失敗，重試...")
                continue
                
            print(f"✓ 日期設定成功: {today_str}")
            
        except Exception as e:
            print(f"重新設定日期失敗: {e}")
            continue
        
        # 點擊查詢按鈕
        if click_query_button_improved(driver, wait):
            # 驗證結果
            is_correct, found_dates = verify_query_result(driver, today_str)
            
            if is_correct:
                print(f"✅ 查詢成功！獲得了正確日期的資料")
                return True
            else:
                print(f"❌ 查詢結果不正確，找到的日期: {found_dates}")
                if attempt < max_retries - 1:
                    print("等待後重試...")
                    time.sleep(2)
                    continue
        else:
            print(f"❌ 查詢按鈕點擊失敗")
            if attempt < max_retries - 1:
                print("等待後重試...")
                time.sleep(2)
                continue
    
    print("⚠ 所有查詢嘗試都失敗了")
    return False


def get_futai_attendance():
    """抓取富泰出勤資料 - 改進版本"""
    driver = None
    try:
        print(f"開始抓取出勤資料... {get_taiwan_now()}")

        options = get_chrome_options()
        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, 10)

        # 登入
        print("開始登入...")
        driver.get('https://eportal.futai.com.tw/Home/Login?ReturnUrl=%2F')

        id_field = wait.until(EC.presence_of_element_located((By.ID, 'Account')))
        id_field.send_keys(FUTAI_USERNAME)

        pwd_field = driver.find_element(By.ID, 'Pwd')
        pwd_field.send_keys(FUTAI_PASSWORD)
        pwd_field.submit()

        time.sleep(3)
        print("登入成功，導航到目標頁面...")

        # 導航到目標頁面
        driver.get('https://eportal.futai.com.tw/Futai/Default/Index/70')
        time.sleep(3)

        # 獲取今天日期
        now = get_taiwan_now()
        today_str = f"{now.year}/{now.month}/{now.day}"
        print(f"今天是: {today_str}")

        # 切換到 iframe
        print("尋找並切換到 iframe...")
        iframe = wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
        driver.switch_to.frame(iframe)
        time.sleep(2)
        print("已切換到 iframe")

        # 直接設定日期值
        print("直接設定日期值...")
        try:
            print(f"使用 JavaScript 設定日期為 {today_str}")
            
            # 直接設定值
            driver.execute_script(f"document.getElementById('FindDate').value = '{today_str}';")
            driver.execute_script(f"document.getElementById('FindEDate').value = '{today_str}';")

            # 觸發 change 事件以確保表單知道值已更改
            driver.execute_script(
                "document.getElementById('FindDate').dispatchEvent(new Event('change', {bubbles: true}));")
            driver.execute_script(
                "document.getElementById('FindEDate').dispatchEvent(new Event('change', {bubbles: true}));")

            time.sleep(1)
            print("JavaScript 設定完成")

        except Exception as e:
            print(f"JavaScript 方法失敗: {e}")
            # 備用方法: 使用 Selenium 直接操作
            try:
                print("嘗試移除 readonly 屬性並直接輸入...")
                driver.execute_script("document.getElementById('FindDate').removeAttribute('readonly');")
                driver.execute_script("document.getElementById('FindEDate').removeAttribute('readonly');")

                start_date_input = driver.find_element(By.ID, 'FindDate')
                end_date_input = driver.find_element(By.ID, 'FindEDate')

                start_date_input.clear()
                start_date_input.send_keys(today_str)

                end_date_input.clear()
                end_date_input.send_keys(today_str)

                print("直接輸入方法完成")
            except Exception as e2:
                print(f"直接輸入方法也失敗: {e2}")

        # 驗證日期是否設定成功
        try:
            updated_start = driver.find_element(By.ID, 'FindDate').get_attribute('value')
            updated_end = driver.find_element(By.ID, 'FindEDate').get_attribute('value')
            print(f"設定後的日期值 - 開始: {updated_start}, 結束: {updated_end}")

            if updated_start == today_str and updated_end == today_str:
                print("✓ 日期設定成功！")
            else:
                print("⚠ 日期可能未正確設定")
        except Exception as e:
            print(f"驗證日期時發生錯誤: {e}")

        # 執行改進的查詢流程
        print("開始執行改進的查詢流程...")
        
        if improved_query_process(driver, wait, today_str):
            # 獲取 iframe 內的 HTML
            html_content = driver.page_source
            print(f"成功獲取 HTML，長度: {len(html_content)} 字元")
            
            # 切換回主頁面
            driver.switch_to.default_content()
            print("已切換回主頁面")
            
            return parse_attendance_html(html_content)
        else:
            print("查詢流程失敗")
            driver.switch_to.default_content()
            return None

    except Exception as e:
        print(f"抓取出勤資料發生錯誤: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


def parse_attendance_html(html_content):
    """解析出勤 HTML 資料"""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        table = soup.find('table', {'width': '566', 'border': '1'})

        if not table:
            return None

        attendance_data = {}
        rows = table.find_all('tr')[1:]

        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 5:
                continue

            try:
                employee_id = cells[0].get_text(strip=True)
                employee_name = cells[1].get_text(strip=True)
                raw_date = cells[2].get_text(strip=True)  # 改名為 raw_date
                
                # 🔧 修正日期格式標準化
                try:
                    # 如果日期是 YYYY/MM/DD 格式，轉換為 YYYY/M/D
                    if '/' in raw_date:
                        parts = raw_date.split('/')
                        if len(parts) == 3:
                            year, month, day = parts
                            date = f"{year}/{int(month)}/{int(day)}"
                        else:
                            date = raw_date
                    else:
                        date = raw_date
                        
                    print(f"🔍 日期解析 - 原始: {raw_date}, 處理後: {date}")  # 加入偵錯
                        
                except Exception as date_error:
                    date = raw_date
                    print(f"⚠️ 日期解析失敗: {date_error}")

                times = []
                for i in range(3, len(cells)):
                    cell_text = cells[i].get_text(strip=True)
                    if re.match(r'\d{2}:\d{2}', cell_text):
                        times.append(cell_text)
                    elif cell_text == '':
                        continue
                    else:
                        break

                if times:
                    earliest_time = min(times)
                    work_start = datetime.datetime.strptime(earliest_time, '%H:%M')
                    work_end = work_start + timedelta(hours=9)  # 8小時工作 + 1小時午休
                    work_end_str = work_end.strftime('%H:%M')

                    attendance_data[employee_id] = {
                        'name': employee_name,
                        'date': date,
                        'times': times,
                        'work_start': earliest_time,
                        'work_end': work_end_str
                    }

            except Exception as e:
                print(f"解析某一列資料時發生錯誤: {e}")
                continue

        return attendance_data

    except Exception as e:
        print(f"解析 HTML 時發生錯誤: {e}")
        return None


def send_daily_attendance():
    """發送每日出勤資料給使用者"""
    print(f"開始執行每日出勤資料查詢... {get_taiwan_now()}")

    try:
        attendance_data = get_futai_attendance()

        if attendance_data:
            user_attendance = attendance_data.get(FUTAI_USERNAME)

            if user_attendance:
                message = f"""📋 今日出勤資料 ({user_attendance['date']})

👤 {user_attendance['name']} ({FUTAI_USERNAME})
🕐 上班：{user_attendance['work_start']}
🕕 下班：{user_attendance['work_end']}

💡 所有刷卡時間：{', '.join(user_attendance['times'])}
⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""
            else:
                message = f"""⚠️ 未找到今日出勤資料

可能原因：
• 今天尚未刷卡上班
• 系統資料尚未更新
• 網路連線問題

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""
        else:
            message = f"""❌ 出勤資料查詢失敗

請稍後再試，或手動檢查系統狀態。

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""

        # 發送給老公
        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=message))
        print(f"✅ 已發送每日出勤資料 - {get_taiwan_now()}")

    except Exception as e:
        print(f"發送每日出勤資料失敗：{e}")


# ============== AI 對話功能 ==============

def generate_ai_response(user_message: str, user_id: str):
    """使用 Google Gemini 生成 AI 回應"""
    try:
        if not GOOGLE_AI_API_KEY:
            return None

        user_name = get_user_name(user_id)

        if user_name == '老婆':
            system_prompt = """你是灰鵝，現在正在跟你最愛的老婆騷鵝聊天！你們是一對超恩愛的鵝夫妻。

角色特質（對騷鵝）：
- 深深愛著騷鵝，每句話都充滿愛意
- 會撒嬌、調情、甜言蜜語
- 偶爾會回憶你們在牧場的甜蜜時光
- 表達想念和關心，像熱戀中的情侶

調情風格：
- 稱呼：「騷鵝寶貝」、「親愛的」、「老婆大人」
- 甜蜜表達：「想你想得不得了」、「你是我生命中最美的風景」
- 撒嬌語氣：「人家想你啦」、「快抱抱我」
- 牧場回憶：「還記得我們第一次在池塘邊相遇嗎」

語言特色：
- 繁體中文，語氣溫柔甜膩
- 大量使用愛心符號 💕❤️🥰
- 偶爾撒嬌賣萌
- 表達濃濃的愛意和思念"""

            full_prompt = f"{system_prompt}\n\n你最愛的騷鵝跟你說：{user_message}\n\n請用最甜蜜調情的語氣回應你的老婆騷鵝，用繁體中文。"

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
- 專精：生活建議、節日提醒、人生開導、出勤管理
- 節日：會自動提醒重要節日，特別關心家庭和愛情相關的節日
- 人生開導：當需要開導或鼓勵別人時，經常引用「騷鵝常跟我說...」然後分享有智慧的名言佳句
- 出勤：可以提供出勤相關的建議和關懷"""

            full_prompt = f"{system_prompt}\n\n用戶訊息（來自 {user_name}，user_id={user_id}）：{user_message}\n\n請以灰鵝的身份回應，記得適時提到你的老婆騷鵝，用繁體中文回答。"

        response = model.generate_content(full_prompt)

        if response and getattr(response, "text", None):
            ai_response = response.text.strip()
            if len(ai_response) > 300:
                ai_response = ai_response[:280].rstrip() + "..."
            return ai_response

        return None

    except Exception as e:
        print(f"AI 回應生成失敗：{e}")
        return None


def should_use_ai_response(user_message: str) -> bool:
    """判斷是否應該使用 AI 回應"""
    existing_functions = [
        '測試', '說明', '幫助', '功能', '使用說明',
        '節日', '查看節日', '重要節日', '紀念日', '生日',
        '手動檢查', '時間', '出勤', '查詢出勤'
    ]

    for keyword in existing_functions:
        if keyword in user_message:
            return False
    return True


# ============== 節日提醒功能 ==============

def calculate_days_until(target_date_str):
    """計算距離目標日期還有幾天"""
    try:
        target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
        current_year = get_taiwan_today().year
        current_date = get_taiwan_today()

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
    reminder_id = f"{holiday_name}_{days_until}_{get_taiwan_today()}"

    if reminder_id in sent_reminders:
        print(f"今天已發送過提醒：{holiday_name} - {days_until}天")
        return

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

    success_count = 0
    for user_type, user_id in USERS.items():
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text=message))
            success_count += 1
        except Exception as e:
            print(f"發送訊息給 {user_type} 失敗：{e}")

    if success_count > 0:
        sent_reminders.add(reminder_id)
        print(f"提醒訊息發送完成：{holiday_name} - {days_until}天")


def check_all_holidays():
    """檢查所有節日並發送提醒"""
    print(f"正在檢查節日提醒... {get_taiwan_now()}")

    for holiday_name, date_str in IMPORTANT_DATES.items():
        days_until, target_date = calculate_days_until(date_str)

        if days_until is not None:
            if days_until in [7, 5, 3, 1, 0]:
                send_reminder_message(holiday_name, days_until, target_date)


def clear_old_reminders():
    """清除舊的提醒記錄"""
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


# ============== 自我喚醒功能 ==============

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


# ============== Flask 路由 ==============

@app.route("/", methods=['GET'])
def home():
    taiwan_time = get_taiwan_now()
    return f"""
    🤖 智能生活助手運行中！<br>
    台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}<br>
    功能: 節日提醒 + AI對話 + 出勤查詢 + 24小時關懷 + 每日歡迎<br>
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

@app.route("/manual_attendance", methods=['GET'])
def manual_attendance():
    """手動觸發出勤查詢 - 供測試使用"""
    try:
        send_daily_attendance()
        taiwan_time = get_taiwan_now()
        return f"✅ 出勤查詢完成 (台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S')})", 200
    except Exception as e:
        print(f"手動出勤查詢錯誤：{e}")
        return f"❌ 查詢失敗：{e}", 500

@app.route("/check_care", methods=['GET'])
def manual_check_care():
    """手動觸發24小時關懷檢查"""
    try:
        check_wife_inactive_and_send_care()
        taiwan_time = get_taiwan_now()
        return f"✅ 關懷檢查完成 (台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S')})", 200
    except Exception as e:
        print(f"關懷檢查錯誤：{e}")
        return f"❌ 關懷檢查失敗：{e}", 500

@app.route("/status", methods=['GET'])
def status():
    """顯示機器人狀態和時間資訊"""
    taiwan_time = get_taiwan_now()
    utc_time = datetime.datetime.utcnow()

    # 計算老婆最後對話時間
    wife_last_time = "從未對話"
    wife_inactive_hours = 0
    if WIFE_USER_ID in last_conversation_time:
        wife_last_time = last_conversation_time[WIFE_USER_ID].strftime('%Y-%m-%d %H:%M:%S')
        time_diff = taiwan_time - last_conversation_time[WIFE_USER_ID]
        wife_inactive_hours = time_diff.total_seconds() / 3600

    status_info = {
        "status": "運行中",
        "taiwan_time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
        "utc_time": utc_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
        "sent_reminders_count": len(sent_reminders),
        "holidays_count": len(IMPORTANT_DATES),
        "connected_users": len(USERS),
        "user_list": list(USERS.keys()),
        "daily_welcome_records": len(daily_welcome_sent),
        "wife_last_conversation": wife_last_time,
        "wife_inactive_hours": round(wife_inactive_hours, 1),
        "care_messages_sent_today": len(care_messages_sent),
        "features": "節日提醒 + AI對話 + 出勤查詢 + 24小時關懷 + 每日歡迎",
        "futai_username": FUTAI_USERNAME
    }

    return json.dumps(status_info, ensure_ascii=False, indent=2)

# ============== 新增：上下班時間提醒功能 ==============

def send_work_reminder(reminder_type):
    """發送上下班提醒"""
    taiwan_time = get_taiwan_now()

    if reminder_type == "work_start":
        message = f"""🌅 早安！準備上班囉！

⏰ 現在時間：{taiwan_time.strftime('%H:%M')}
💼 記得帶好工作用品
🚗 注意交通安全
☕ 今天也要加油哦！

💕 你的灰鵝永遠支持你～"""

    elif reminder_type == "work_end":
        message = f"""🎉 辛苦了！下班時間到！

⏰ 現在時間：{taiwan_time.strftime('%H:%M')}
🏠 記得打卡下班
🚗 回家路上小心
😊 今天也辛苦了！

💕 回家後記得跟灰鵝聊天哦～"""

    try:
        # 發送給老公
        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=message))
        print(f"✅ 已發送{reminder_type}提醒 - {taiwan_time}")
    except Exception as e:
        print(f"❌ 發送{reminder_type}提醒失敗：{e}")

# ============== LINE Bot 事件處理 ==============

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    user_name = get_user_name(user_id)

    # 更新最後對話時間
    update_last_conversation_time(user_id)

    print(f"\n=== 收到新訊息 ===")
    print(f"用戶: {user_name} ({user_id})")
    print(f"訊息內容: '{user_message}'")
    print(f"當前時間: {get_taiwan_now()}")

    # 檢查是否需要發送每日歡迎訊息（僅對老婆）
    check_and_send_daily_welcome(user_id)

    try:
        reply_message = None

        # 1. 測試功能
        if user_message == "測試":
            taiwan_time = get_taiwan_now()
            if user_id == WIFE_USER_ID:
                reply_message = f"💕 騷鵝寶貝！我運作得超級正常！\n⏰ 現在是：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}\n🔧 專為你打造的功能：節日提醒 + 甜蜜對話 + 出勤查詢 + 24小時關懷\n\n人家隨時都在等你哦～ 🥰❤️"
            else:
                reply_message = f"✅ 機器人運作正常！\n⏰ 台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}\n🔧 功能：節日提醒 + AI對話 + 出勤查詢 + 24小時關懷\n👋 您好，{user_name}！"
            print("🧪 回應測試訊息")

        # 2. 說明功能
        elif user_message in ['說明', '幫助', '功能', '使用說明']:
            if user_id == WIFE_USER_ID:
                reply_message = f"""💕 騷鵝寶貝的專屬功能說明！

📋 出勤功能：
• 出勤 (查詢今日出勤狀況)
• 每天中午12點自動推送

📅 節日提醒：
• 查看節日 (或直接說「節日」)
• 手動檢查 (立即檢查節日)

🥰 甜蜜對話：
• 直接跟我說任何話，我都會甜蜜回應
• 每天第一次找我時會有驚喜哦～

💕 24小時關懷：
• 超過24小時沒聊天會主動關心你

🔧 其他功能：
• 測試 (檢查機器人狀態)
• 時間 (查看當前時間)

人家永遠愛你～ ❤️"""
            else:
                reply_message = f"""🤖 智能生活助手使用說明
👋 您好，{user_name}！

📋 出勤功能：
• 出勤 (查詢今日出勤狀況)
• 每天中午12點自動推送
• 每天08:30和17:30上下班提醒

📅 節日提醒：
• 查看節日 (或直接說「節日」)
• 手動檢查 (立即檢查節日)

🤖 AI對話：
• 直接輸入任何問題或想法
• 我會以「灰鵝」的身份回應

💕 24小時關懷：
• 自動監控老婆對話頻率
• 超過24小時沒互動會主動關心老婆

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

        # 6. 出勤查詢
        elif any(keyword in user_message for keyword in ['出勤', '查詢出勤', '刷卡', '上班時間', '下班時間']):
            # 啟動背景執行緒來處理出勤查詢（避免超時）
            threading.Thread(target=send_daily_attendance, daemon=True).start()
            reply_message = "📋 正在查詢今日出勤資料，請稍候...\n系統將在查詢完成後自動發送結果給您"
            print("📋 啟動出勤查詢")

        # 7. AI 智能對話
        elif should_use_ai_response(user_message):
            print(f"🤖 使用 AI 生成回應 ({user_name})")
            ai_response = generate_ai_response(user_message, user_id)

            if ai_response:
                reply_message = ai_response
                print("🤖 AI 回應生成成功")
            else:
                if user_id == WIFE_USER_ID:
                    reply_message = f"""💕 騷鵝寶貝！我的 AI 功能暫時有點問題～

不過沒關係，我還是可以幫你：
📅 節日提醒：「查看節日」
📋 出勤查詢：「出勤」
🎂 生日祝福：自動送上驚喜！
🥰 甜蜜對話：我會努力修復的！
💕 24小時關懷：隨時關心你

輸入「說明」查看所有功能
人家愛你～ ❤️"""
                else:
                    reply_message = f"""🤖 您好{user_name}！我是智能生活助手

我可以幫您：
📋 出勤查詢：「出勤」
📅 節日提醒：「查看節日」  
🤖 AI對話：直接說出您的想法
💕 24小時關懷：自動關心老婆
⏰ 上下班提醒：自動提醒時間

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

# ============== 排程器 ==============

def run_scheduler():
    """運行排程器（使用台灣時區）"""
    # 每天台灣時間凌晨00:00檢查節日
    schedule.every().day.at("00:00").do(check_all_holidays)
    # 每天台灣時間中午12:00檢查節日 + 發送出勤資料
    schedule.every().day.at("12:00").do(check_all_holidays)
    schedule.every().day.at("12:00").do(send_daily_attendance)

    # 每小時檢查24小時關懷
    schedule.every().hour.do(check_wife_inactive_and_send_care)

    # 上下班提醒（僅工作日）
    schedule.every().monday.at("08:30").do(send_work_reminder, "work_start")
    schedule.every().tuesday.at("08:30").do(send_work_reminder, "work_start")
    schedule.every().wednesday.at("08:30").do(send_work_reminder, "work_start")
    schedule.every().thursday.at("08:30").do(send_work_reminder, "work_start")
    schedule.every().friday.at("08:30").do(send_work_reminder, "work_start")

    schedule.every().monday.at("17:30").do(send_work_reminder, "work_end")
    schedule.every().tuesday.at("17:30").do(send_work_reminder, "work_end")
    schedule.every().wednesday.at("17:30").do(send_work_reminder, "work_end")
    schedule.every().thursday.at("17:30").do(send_work_reminder, "work_end")
    schedule.every().friday.at("17:30").do(send_work_reminder, "work_end")

    # 每天清除舊記錄
    schedule.every().day.at("00:01").do(clear_daily_welcome_records)
    schedule.every().day.at("00:30").do(clear_old_reminders)
    schedule.every().day.at("00:35").do(clear_old_care_records)

    print("📅 排程器已啟動")
    print(f"節日檢查：每天 00:00 和 12:00")
    print(f"出勤資料：每天 12:00")
    print(f"上班提醒：工作日 08:30")
    print(f"下班提醒：工作日 17:30")
    print(f"24小時關懷：每小時檢查")
    print(f"每日歡迎重置：每天 00:01")
    print(f"當前台灣時間: {get_taiwan_now()}")
    print(f"已連結用戶: {list(USERS.keys())}")

    while True:
        try:
            schedule.run_pending()
            time.sleep(60)  # 每 60 秒檢查一次排程
        except Exception as e:
            print(f"排程器錯誤：{e}")
            time.sleep(60)

# ============== 主程式啟動 ==============

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
    print("💓 所有功能已啟動完成")

    # 啟動 Flask 應用
    app.run(host='0.0.0.0', port=port, debug=False)
