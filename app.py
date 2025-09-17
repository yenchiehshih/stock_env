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
    'husband': os.environ.get('YOUR_USER_ID'),
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


def safe_print(message: str, level: str = "INFO"):
    """統一的日誌輸出函數，包含台灣時間和等級"""
    try:
        taiwan_time = get_taiwan_now()
        formatted_time = taiwan_time.strftime('%Y-%m-%d %H:%M:%S TW')
        print(f"[{formatted_time}] [{level}] {message}")
    except Exception as e:
        # 如果時間格式化失敗，至少輸出基本訊息
        print(f"[TIME_ERROR] [{level}] {message} (時間格式化錯誤: {e})")


# ============== OOP 重構：狀態管理類別 ==============

class ReminderManager:
    """管理節日提醒的狀態"""

    def __init__(self):
        self.sent_reminders = set()

    def is_reminder_sent(self, holiday_name: str, days_until: int) -> bool:
        """檢查是否已發送過提醒"""
        today_str = str(get_taiwan_today())
        reminder_id = f"{holiday_name}_{days_until}_{today_str}"
        return reminder_id in self.sent_reminders

    def mark_reminder_sent(self, holiday_name: str, days_until: int):
        """標記提醒已發送"""
        today_str = str(get_taiwan_today())
        reminder_id = f"{holiday_name}_{days_until}_{today_str}"
        self.sent_reminders.add(reminder_id)
        safe_print(f"標記提醒已發送: {reminder_id}", "DEBUG")

    def clear_old_reminders(self):
        """清除舊的提醒記錄"""
        today_str = str(get_taiwan_today())
        old_count = len(self.sent_reminders)
        self.sent_reminders = {r for r in self.sent_reminders if today_str in r}
        new_count = len(self.sent_reminders)
        safe_print(f"清除舊提醒記錄: {old_count} -> {new_count}", "INFO")


class WelcomeManager:
    """管理每日歡迎訊息的狀態"""

    def __init__(self):
        self.daily_welcome_sent = set()

    def is_welcome_sent_today(self, user_id: str) -> bool:
        """檢查今天是否已發送歡迎訊息"""
        if user_id != WIFE_USER_ID:
            return True  # 只對老婆發送歡迎訊息

        today_str = str(get_taiwan_today())
        welcome_key = f"wife_welcome_{today_str}"
        return welcome_key in self.daily_welcome_sent

    def mark_welcome_sent(self, user_id: str):
        """標記歡迎訊息已發送"""
        today_str = str(get_taiwan_today())
        welcome_key = f"wife_welcome_{today_str}"
        self.daily_welcome_sent.add(welcome_key)
        safe_print(f"標記歡迎訊息已發送: {welcome_key}", "DEBUG")

    def clear_old_records(self):
        """清除舊的歡迎記錄"""
        today_str = str(get_taiwan_today())
        old_count = len(self.daily_welcome_sent)
        self.daily_welcome_sent = {record for record in self.daily_welcome_sent if today_str in record}
        new_count = len(self.daily_welcome_sent)
        safe_print(f"清除舊歡迎記錄: {old_count} -> {new_count}", "INFO")


class CareManager:
    """管理24小時關懷功能的狀態"""

    def __init__(self):
        self.last_conversation_time = {}
        self.care_messages_sent = set()

    def update_last_conversation_time(self, user_id: str):
        """更新最後對話時間"""
        current_time = get_taiwan_now()
        self.last_conversation_time[user_id] = current_time
        user_name = get_user_name(user_id)
        safe_print(f"更新 {user_name} 的最後對話時間: {current_time.strftime('%Y-%m-%d %H:%M:%S')}", "DEBUG")

    def should_send_care_message(self, user_id: str) -> tuple[bool, int]:
        """檢查是否應該發送關心訊息，返回 (是否發送, 小時數)"""
        if user_id != WIFE_USER_ID:
            return False, 0

        if user_id not in self.last_conversation_time:
            safe_print("老婆從未對話過，不發送關心訊息", "INFO")
            return False, 0

        current_time = get_taiwan_now()
        last_time = self.last_conversation_time[user_id]
        time_diff = current_time - last_time
        hours_since = time_diff.total_seconds() / 3600

        if hours_since > 24:
            today_str = current_time.strftime('%Y-%m-%d')
            care_message_id = f"wife_care_{today_str}"

            if care_message_id not in self.care_messages_sent:
                return True, int(hours_since)

        return False, int(hours_since)

    def mark_care_message_sent(self):
        """標記關心訊息已發送"""
        current_time = get_taiwan_now()
        today_str = current_time.strftime('%Y-%m-%d')
        care_message_id = f"wife_care_{today_str}"
        self.care_messages_sent.add(care_message_id)
        safe_print(f"標記關心訊息已發送: {care_message_id}", "DEBUG")

    def clear_old_records(self):
        """清除舊的關心訊息記錄"""
        today_str = get_taiwan_today().strftime('%Y-%m-%d')
        old_count = len(self.care_messages_sent)
        self.care_messages_sent = {record for record in self.care_messages_sent if today_str in record}
        new_count = len(self.care_messages_sent)
        safe_print(f"清除舊關心記錄: {old_count} -> {new_count}", "INFO")


class WorkManager:
    """管理工作出勤相關的狀態"""

    def __init__(self):
        self.daily_work_end_time = None
        self.work_end_reminders_set = False
        self.work_end_reminders_sent = set()

    def set_work_end_time(self, work_end_str: str):
        """設定今日下班時間"""
        self.daily_work_end_time = work_end_str
        safe_print(f"設定今日預估下班時間: {work_end_str}", "INFO")

    def setup_work_end_reminders(self, work_end_str: str):
        """根據下班時間設定動態提醒"""
        try:
            # 解析下班時間
            work_end_time = datetime.datetime.strptime(work_end_str, '%H:%M').time()
            today = get_taiwan_today()
            work_end_datetime = datetime.datetime.combine(today, work_end_time)
            work_end_datetime = TAIWAN_TZ.localize(work_end_datetime)

            # 計算提醒時間
            reminder_times = {
                '1小時前': work_end_datetime - timedelta(hours=1),
                '30分鐘前': work_end_datetime - timedelta(minutes=30),
                '10分鐘前': work_end_datetime - timedelta(minutes=10),
                '5分鐘前': work_end_datetime - timedelta(minutes=5)
            }

            current_time = get_taiwan_now()

            safe_print(f"設定下班提醒 - 預估下班時間: {work_end_str}", "INFO")
            for desc, reminder_time in reminder_times.items():
                if reminder_time > current_time:
                    safe_print(f"  {desc}: {reminder_time.strftime('%H:%M')}", "DEBUG")
                else:
                    safe_print(f"  {desc}: {reminder_time.strftime('%H:%M')} (已過時)", "WARNING")

            self.work_end_reminders_set = True

        except Exception as e:
            safe_print(f"設定下班提醒失敗: {e}", "ERROR")
            self.work_end_reminders_set = False

    def check_work_end_reminders(self):
        """檢查是否需要發送下班提醒"""
        if not self.daily_work_end_time:
            return

        try:
            # 解析下班時間
            work_end_time = datetime.datetime.strptime(self.daily_work_end_time, '%H:%M').time()
            today = get_taiwan_today()
            work_end_datetime = datetime.datetime.combine(today, work_end_time)
            work_end_datetime = TAIWAN_TZ.localize(work_end_datetime)

            current_time = get_taiwan_now()

            # 檢查各個提醒點
            reminder_configs = [
                {'minutes': 60, 'desc': '1小時前', 'key': '60min'},
                {'minutes': 30, 'desc': '30分鐘前', 'key': '30min'},
                {'minutes': 10, 'desc': '10分鐘前', 'key': '10min'},
                {'minutes': 5, 'desc': '5分鐘前', 'key': '5min'}
            ]

            today_str = today.strftime('%Y-%m-%d')

            for config in reminder_configs:
                reminder_time = work_end_datetime - timedelta(minutes=config['minutes'])
                reminder_id = f"work_end_{config['key']}_{today_str}"

                # 檢查是否到了提醒時間（只在時間到了或過了才提醒）
                time_diff = (current_time - reminder_time).total_seconds()

                # 如果當前時間已經過了提醒時間，且在2分鐘內（避免重複提醒）
                if 0 <= time_diff <= 120 and reminder_id not in self.work_end_reminders_sent:
                    send_work_end_reminder(config['desc'], self.daily_work_end_time)
                    self.work_end_reminders_sent.add(reminder_id)
                    safe_print(f"已發送下班提醒：{config['desc']}", "INFO")

        except Exception as e:
            safe_print(f"檢查下班提醒時發生錯誤: {e}", "ERROR")

    def clear_work_end_records(self):
        """清除下班提醒相關記錄"""
        self.daily_work_end_time = None
        self.work_end_reminders_set = False
        self.work_end_reminders_sent.clear()
        safe_print("已清除下班提醒記錄", "INFO")


# ============== 初始化管理器實例 ==============

reminder_manager = ReminderManager()
welcome_manager = WelcomeManager()
care_manager = CareManager()
work_manager = WorkManager()


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
        safe_print(f"已發送老婆每日歡迎訊息", "INFO")
        return True
    except Exception as e:
        safe_print(f"發送老婆歡迎訊息失敗：{e}", "ERROR")
        return False


def check_and_send_daily_welcome(user_id):
    """檢查是否需要發送每日歡迎訊息"""
    if welcome_manager.is_welcome_sent_today(user_id):
        return False

    if user_id == WIFE_USER_ID:
        success = send_wife_welcome_message()
        if success:
            welcome_manager.mark_welcome_sent(user_id)
        return success

    return False


# ============== 24小時關懷功能 ==============

def check_wife_inactive_and_send_care():
    """檢查老婆是否超過24小時沒對話，如果是則發送關心訊息"""
    should_send, hours_since = care_manager.should_send_care_message(WIFE_USER_ID)

    if should_send:
        care_message = generate_care_message_for_wife(hours_since)

        try:
            line_bot_api.push_message(WIFE_USER_ID, TextSendMessage(text=care_message))
            care_manager.mark_care_message_sent()
            safe_print(f"已發送關心訊息給騷鵝 - 她已 {hours_since} 小時沒對話", "INFO")
        except Exception as e:
            safe_print(f"發送關心訊息失敗：{e}", "ERROR")


def generate_care_message_for_wife(hours_since: int) -> str:
    """生成關心訊息"""
    messages = [
        f"💕 騷鵝寶貝～我們已經 {hours_since} 小時沒聊天了呢！\n\n人家在牧場裡好想你呀～ 🥺\n最近過得如何呢？有什麼開心或煩惱的事都可以跟我分享哦！",

        f"🤗 親愛的騷鵝，我發現我們已經 {hours_since} 小時沒有對話了～\n\n不知道你最近在忙什麼呢？\n記得要好好照顧自己，有我這隻灰鵝永遠在這裡陪你！ ❤️",
    ]

    return random.choice(messages)


# ============== 出勤查詢功能 ==============
def get_chrome_options():
    """設定 Chrome 選項（適合 Render 環境）"""
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-plugins')
    options.add_argument('--single-process')  # 新增：強制單程序模式
    options.add_argument('--memory-pressure-off')  # 新增：關閉記憶體壓力檢測
    options.add_argument('--disable-background-timer-throttling')  # 新增：穩定性
    options.add_argument('--disable-renderer-backgrounding')  # 新增：穩定性
    options.add_argument('--disable-backgrounding-occluded-windows')  # 新增：穩定性
    options.add_argument('--window-size=1024,768')  # 改小：減少記憶體使用
    return options


def click_query_button_improved(driver, wait):
    """改進的查詢按鈕點擊方法"""
    safe_print("尋找並點擊查詢按鈕...", "DEBUG")

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
        safe_print(f"點擊前頁面 hash: {pre_click_hash}", "DEBUG")

        # 多種方式嘗試點擊
        click_success = False

        # 方法1: 普通點擊
        try:
            query_button.click()
            safe_print("使用普通點擊", "DEBUG")
            click_success = True
        except Exception as e:
            safe_print(f"普通點擊失敗: {e}", "WARNING")

        # 方法2: JavaScript 點擊
        if not click_success:
            try:
                driver.execute_script("arguments[0].click();", query_button)
                safe_print("使用 JavaScript 點擊", "DEBUG")
                click_success = True
            except Exception as e:
                safe_print(f"JavaScript 點擊失敗: {e}", "WARNING")

        # 方法3: 模擬 Enter 鍵
        if not click_success:
            try:
                query_button.send_keys(Keys.RETURN)
                safe_print("使用 Enter 鍵觸發", "DEBUG")
                click_success = True
            except Exception as e:
                safe_print(f"Enter 鍵觸發失敗: {e}", "WARNING")

        if not click_success:
            raise Exception("所有點擊方法都失敗了")

        # 等待頁面更新 - 使用多種方法驗證
        safe_print("等待查詢結果載入...", "DEBUG")

        # 方法1: 等待頁面內容變化
        max_wait_time = 15
        start_time = time.time()

        while time.time() - start_time < max_wait_time:
            time.sleep(1)
            current_html = driver.page_source
            current_hash = hash(current_html)

            # 檢查頁面是否有變化
            if current_hash != pre_click_hash:
                safe_print(f"檢測到頁面內容變化 (等待了 {time.time() - start_time:.1f} 秒)", "DEBUG")
                break

            safe_print(f"等待中... ({time.time() - start_time:.1f}s)", "DEBUG")
        else:
            safe_print("警告: 超時未檢測到頁面變化", "WARNING")

        # 方法2: 等待特定的載入指標消失或出現
        try:
            # 假設有載入指標，等待它消失
            WebDriverWait(driver, 5).until_not(
                EC.presence_of_element_located((By.CLASS_NAME, "loading"))
            )
            safe_print("載入指標已消失", "DEBUG")
        except:
            safe_print("沒有找到載入指標，繼續執行", "DEBUG")

        # 方法3: 額外等待時間確保資料完全載入
        time.sleep(3)
        safe_print("查詢完成，準備抓取結果", "DEBUG")

        return True

    except Exception as e:
        safe_print(f"查詢按鈕點擊失敗: {e}", "ERROR")
        return False


def verify_query_result(driver, expected_date):
    """驗證查詢結果是否正確"""
    safe_print(f"驗證查詢結果是否包含日期: {expected_date}", "DEBUG")

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
                safe_print(f"找到完整日期: {date_format}", "DEBUG")
                found_date = True
                break

        if not found_date:
            # 列出頁面中實際找到的日期
            dates_in_page = re.findall(r'\d{4}/\d{1,2}/\d{1,2}', html_content)
            if dates_in_page:
                safe_print(f"頁面中實際包含的日期: {set(dates_in_page)}", "WARNING")
                return False, dates_in_page
            else:
                safe_print("頁面中未找到任何日期格式", "WARNING")
                return False, []

        return True, []

    except Exception as e:
        safe_print(f"驗證查詢結果時發生錯誤: {e}", "ERROR")
        return False, []


def improved_query_process(driver, wait, today_str):
    """改進的查詢流程"""
    max_retries = 3

    for attempt in range(max_retries):
        safe_print(f"查詢嘗試 {attempt + 1}/{max_retries}", "INFO")

        # 重新設定日期（確保每次嘗試都是最新的）
        try:
            safe_print("重新設定查詢日期...", "DEBUG")
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
            safe_print(f"設定後的日期值 - 開始: {updated_start}, 結束: {updated_end}", "DEBUG")

            if updated_start != today_str or updated_end != today_str:
                safe_print("日期設定失敗，重試...", "WARNING")
                continue

            safe_print(f"日期設定成功: {today_str}", "DEBUG")

        except Exception as e:
            safe_print(f"重新設定日期失敗: {e}", "ERROR")
            continue

        # 點擊查詢按鈕
        if click_query_button_improved(driver, wait):
            # 驗證結果
            is_correct, found_dates = verify_query_result(driver, today_str)

            if is_correct:
                safe_print(f"查詢成功！獲得了正確日期的資料", "INFO")
                return True
            else:
                safe_print(f"查詢結果不正確，找到的日期: {found_dates}", "WARNING")
                if attempt < max_retries - 1:
                    safe_print("等待後重試...", "INFO")
                    time.sleep(2)
                    continue
        else:
            safe_print(f"查詢按鈕點擊失敗", "ERROR")
            if attempt < max_retries - 1:
                safe_print("等待後重試...", "INFO")
                time.sleep(2)
                continue

    safe_print("所有查詢嘗試都失敗了", "ERROR")
    return False


def get_futai_attendance():
    """抓取富台出勤資料（修正版本）"""
    driver = None
    try:
        safe_print(f"開始抓取出勤資料...", "INFO")

        options = get_chrome_options()
        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, 10)

        # 登入
        driver.get('https://eportal.futai.com.tw/Home/Login?ReturnUrl=%2F')
        
        id_field = wait.until(EC.presence_of_element_located((By.ID, 'Account')))
        id_field.send_keys(FUTAI_USERNAME)

        pwd_field = driver.find_element(By.ID, 'Pwd')
        pwd_field.send_keys(FUTAI_PASSWORD)
        pwd_field.submit()

        time.sleep(3)

        # 使用測試成功的URL（關鍵修改）
        driver.get('https://bpmflow.futai.com.tw/futaibpmflow/SignOnFutai.aspx?Account=2993&Token=QxY%2BV82RudxNLWk6ZPWQdiDWxUmcDvnLTJUKvhMIG08%3D&FunctionID=AB-ABS-04')
        
        time.sleep(3)

        # 獲取今天日期
        now = get_taiwan_now()
        today_str = f"{now.year}/{now.month}/{now.day}"

        # 直接設定日期（不需要iframe）
        driver.execute_script(f"document.getElementById('FindDate').value = '{today_str}';")
        driver.execute_script(f"document.getElementById('FindEDate').value = '{today_str}';")

        time.sleep(1)

        # 點擊查詢按鈕
        query_button = driver.find_element(By.XPATH, "//input[@name='Submit' and @value='查詢']")
        driver.execute_script("arguments[0].click();", query_button)

        time.sleep(5)

        # 直接獲取HTML（不需要切換iframe）
        html_content = driver.page_source
        return parse_attendance_html(html_content)

    except Exception as e:
        safe_print(f"抓取出勤資料發生錯誤: {e}", "ERROR")
        return None
    finally:
        if driver:
            try:
                driver.quit()
                time.sleep(2)
                import gc
                gc.collect()
            except:
                pass
                
def parse_attendance_html(html_content):
    """解析出勤 HTML 資料（更新版本）"""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        table = soup.find('table', {'width': '566', 'border': '1'})

        if not table:
            safe_print("找不到出勤資料表格", "WARNING")
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
                raw_date = cells[2].get_text(strip=True)

                # 修正日期格式標準化
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

                    safe_print(f"日期解析 - 原始: {raw_date}, 處理後: {date}", "DEBUG")

                except Exception as date_error:
                    date = raw_date
                    safe_print(f"日期解析失敗: {date_error}", "WARNING")

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
                safe_print(f"解析某一列資料時發生錯誤: {e}", "ERROR")
                continue

        safe_print(f"解析完成，找到 {len(attendance_data)} 筆出勤資料", "INFO")
        return attendance_data

    except Exception as e:
        safe_print(f"解析 HTML 時發生錯誤: {e}", "ERROR")
        return None

def send_daily_attendance():
    """發送每日出勤資料給使用者（老公和老婆都會收到）"""
    safe_print(f"開始執行每日出勤資料查詢...", "INFO")

    try:
        attendance_data = get_futai_attendance()

        if attendance_data:
            user_attendance = attendance_data.get(FUTAI_USERNAME)

            if user_attendance:
                # 取得下班時間並設定動態提醒（只為老公設定）
                work_end_str = user_attendance['work_end']  # 格式: "17:30"
                work_manager.set_work_end_time(work_end_str)

                # 設定今日的下班提醒（只為老公設定）
                work_manager.setup_work_end_reminders(work_end_str)

                # 給老公的詳細出勤資料訊息
                husband_message = f"""📋 今日出勤資料 ({user_attendance['date']})

👤 {user_attendance['name']} ({FUTAI_USERNAME})
🕐 上班：{user_attendance['work_start']}
🕕 預估下班：{user_attendance['work_end']}

💡 所有刷卡時間：{', '.join(user_attendance['times'])}
⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}

🔔 已設定下班前提醒：1小時、30分鐘、10分鐘、5分鐘"""

                # 給騷鵝的溫馨出勤資料訊息
                wife_message = f"""💕 騷鵝寶貝，灰鵝的出勤資料來囉～

📅 日期：{user_attendance['date']}
🌅 上班時間：{user_attendance['work_start']}
🌅 預估下班：{user_attendance['work_end']}

💖 你的灰鵝今天也再努力工作，為了我們的未來加油！
騷鵝在外送的時候要注意安全💕騎車不要太快！
記得晚上要誇誇在牧場等你外送回家的灰鵝哦～

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""

            else:
                # 沒有找到出勤資料的訊息
                husband_message = f"""⚠️ 未找到今日出勤資料

可能原因：
• 今天尚未刷卡上班
• 系統資料尚未更新
• 網路連線問題

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""

                wife_message = f"""💕 騷鵝寶貝～

今天還沒查到灰鵝的出勤資料呢，可能是：
• 灰鵝還沒到公司刷卡
• 系統還沒更新資料
• 網路有點問題

不過不用擔心，等等再查查看！

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""

        else:
            # 查詢失敗的訊息
            husband_message = f"""❌ 出勤資料查詢失敗

請稍後再試，或手動檢查系統狀態。

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""

            wife_message = f"""💕 騷鵝寶貝～

灰鵝的出勤查詢出了點小問題，可能是系統在維護中。
不過別擔心，你的灰鵝會想辦法處理的！

等等會再試試看的～

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""

        # 發送給老公
        try:
            line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=husband_message))
            safe_print(f"已發送每日出勤資料給老公", "INFO")
        except Exception as e:
            safe_print(f"發送出勤資料給老公失敗：{e}", "ERROR")

        # 發送給騷鵝（老婆）
        try:
            line_bot_api.push_message(WIFE_USER_ID, TextSendMessage(text=wife_message))
            safe_print(f"已發送每日出勤資料給騷鵝", "INFO")
        except Exception as e:
            safe_print(f"發送出勤資料給騷鵝失敗：{e}", "ERROR")

    except Exception as e:
        safe_print(f"發送每日出勤資料失敗：{e}", "ERROR")


def send_work_end_reminder(time_desc, work_end_time):
    """發送下班提醒訊息"""
    taiwan_time = get_taiwan_now()

    message = f"""🏠 下班提醒 - {time_desc}

⏰ 現在時間：{taiwan_time.strftime('%H:%M')}
🕕 預估下班：{work_end_time}
📋 記得打卡下班哦！

💡 溫馨提醒：
• 整理好桌面和文件
• 確認明天的工作安排
• 注意回家路上的交通安全

💕 辛苦了！你的騷鵝在家等你～"""

    try:
        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=message))
        safe_print(f"已發送{time_desc}下班提醒", "INFO")
    except Exception as e:
        safe_print(f"發送{time_desc}下班提醒失敗：{e}", "ERROR")


# ============== AI 對話功能 ==============

def generate_ai_response(user_message: str, user_id: str):
    """使用 Google Gemini 生成 AI 回應"""
    try:
        if not GOOGLE_AI_API_KEY:
            safe_print("Google AI API Key 未設定", "WARNING")
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

        safe_print(f"開始生成 AI 回應給 {user_name}", "DEBUG")
        response = model.generate_content(full_prompt)

        if response and getattr(response, "text", None):
            ai_response = response.text.strip()
            if len(ai_response) > 300:
                ai_response = ai_response[:280].rstrip() + "..."
            safe_print(f"AI 回應生成成功，長度: {len(ai_response)} 字", "DEBUG")
            return ai_response

        safe_print("AI 回應為空", "WARNING")
        return None

    except Exception as e:
        safe_print(f"AI 回應生成失敗：{e}", "ERROR")
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
    except ValueError as e:
        safe_print(f"日期計算錯誤: {e}", "ERROR")
        return None, None


def send_reminder_message(holiday_name, days_until, target_date):
    """發送提醒訊息給所有用戶"""
    if reminder_manager.is_reminder_sent(holiday_name, days_until):
        safe_print(f"今天已發送過提醒：{holiday_name} - {days_until}天", "DEBUG")
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
            safe_print(f"發送訊息給 {user_type} 失敗：{e}", "ERROR")

    if success_count > 0:
        reminder_manager.mark_reminder_sent(holiday_name, days_until)
        safe_print(f"提醒訊息發送完成：{holiday_name} - {days_until}天", "INFO")


def check_all_holidays():
    """檢查所有節日並發送提醒"""
    safe_print(f"正在檢查節日提醒...", "INFO")

    for holiday_name, date_str in IMPORTANT_DATES.items():
        days_until, target_date = calculate_days_until(date_str)

        if days_until is not None:
            if days_until in [7, 5, 3, 1, 0]:
                send_reminder_message(holiday_name, days_until, target_date)


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
        safe_print("未設定 RENDER_EXTERNAL_URL，跳過自我喚醒功能", "WARNING")
        return

    while True:
        try:
            time.sleep(25 * 60)  # 等待 25 分鐘
            response = requests.get(f"{app_url}/", timeout=10)
            safe_print(f"自我喚醒完成 - Status: {response.status_code}", "DEBUG")
        except Exception as e:
            safe_print(f"自我喚醒失敗：{e}", "ERROR")


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
        safe_print("Invalid signature", "ERROR")
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
        safe_print(f"手動檢查錯誤：{e}", "ERROR")
        return f"❌ 檢查失敗：{e}", 500


@app.route("/manual_attendance", methods=['GET'])
def manual_attendance():
    """手動觸發出勤查詢 - 供測試使用"""
    try:
        send_daily_attendance()
        taiwan_time = get_taiwan_now()
        return f"✅ 出勤查詢完成 (台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S')})", 200
    except Exception as e:
        safe_print(f"手動出勤查詢錯誤：{e}", "ERROR")
        return f"❌ 查詢失敗：{e}", 500


@app.route("/check_care", methods=['GET'])
def manual_check_care():
    """手動觸發24小時關懷檢查"""
    try:
        check_wife_inactive_and_send_care()
        taiwan_time = get_taiwan_now()
        return f"✅ 關懷檢查完成 (台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S')})", 200
    except Exception as e:
        safe_print(f"關懷檢查錯誤：{e}", "ERROR")
        return f"❌ 關懷檢查失敗：{e}", 500


@app.route("/status", methods=['GET'])
def status():
    """顯示機器人狀態和時間資訊"""
    taiwan_time = get_taiwan_now()
    utc_time = datetime.datetime.now(datetime.timezone.utc)

    # 計算老婆最後對話時間
    wife_last_time = "從未對話"
    wife_inactive_hours = 0
    if WIFE_USER_ID in care_manager.last_conversation_time:
        wife_last_time = care_manager.last_conversation_time[WIFE_USER_ID].strftime('%Y-%m-%d %H:%M:%S')
        time_diff = taiwan_time - care_manager.last_conversation_time[WIFE_USER_ID]
        wife_inactive_hours = time_diff.total_seconds() / 3600

    status_info = {
        "status": "運行中",
        "taiwan_time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
        "utc_time": utc_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
        "sent_reminders_count": len(reminder_manager.sent_reminders),
        "holidays_count": len(IMPORTANT_DATES),
        "connected_users": len(USERS),
        "user_list": list(USERS.keys()),
        "daily_welcome_records": len(welcome_manager.daily_welcome_sent),
        "wife_last_conversation": wife_last_time,
        "wife_inactive_hours": round(wife_inactive_hours, 1),
        "care_messages_sent_today": len(care_manager.care_messages_sent),
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
        safe_print(f"已發送{reminder_type}提醒", "INFO")
    except Exception as e:
        safe_print(f"發送{reminder_type}提醒失敗：{e}", "ERROR")


# ============== Line Bot 事件處理 ==============

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """處理 Line Bot 接收到的訊息"""
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    user_name = get_user_name(user_id)

    safe_print(f"收到 {user_name} 的訊息: {user_message}", "INFO")

    # 更新用戶最後對話時間
    care_manager.update_last_conversation_time(user_id)

    # 檢查是否需要發送每日歡迎訊息
    check_and_send_daily_welcome(user_id)

    # 根據訊息內容決定回應方式
    if user_message in ['測試', '功能測試', 'test']:
        reply_text = get_test_message()

    elif any(keyword in user_message for keyword in ['說明', '幫助', '功能', '使用說明']):
        reply_text = get_help_message()

    elif any(keyword in user_message for keyword in ['節日', '查看節日', '重要節日', '紀念日', '生日']):
        reply_text = list_all_holidays()

    elif user_message == '手動檢查':
        check_all_holidays()
        taiwan_time = get_taiwan_now()
        reply_text = f"已手動執行節日檢查！\n台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}"

    elif user_message in ['時間', '現在時間', '台灣時間']:
        taiwan_time = get_taiwan_now()
        reply_text = f"🕐 台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}\n星期{['一', '二', '三', '四', '五', '六', '日'][taiwan_time.weekday()]}"

    elif any(keyword in user_message for keyword in ['出勤', '查詢出勤', '刷卡', '上班時間', '下班時間']):
        if user_id == YOUR_USER_ID:
            threading.Thread(target=send_daily_attendance, daemon=True).start()
            reply_text = "📋 正在查詢今日出勤資料，請稍候...\n系統將在查詢完成後自動發送結果給您"
            safe_print("📋 啟動出勤查詢", "INFO")
        else:
            reply_text = "抱歉，出勤查詢功能僅限特定用戶使用。"

    else:
        # 使用 AI 回應
        if should_use_ai_response(user_message):
            ai_response = generate_ai_response(user_message, user_id)
            if ai_response:
                reply_text = ai_response
            else:
                reply_text = get_fallback_response(user_name)
        else:
            reply_text = get_fallback_response(user_name)

    # 發送回覆
    try:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        safe_print(f"已回覆 {user_name}: {reply_text[:50]}...", "DEBUG")
    except LineBotApiError as e:
        safe_print(f"Line API 錯誤：{e}", "ERROR")
    except Exception as e:
        safe_print(f"回覆訊息失敗：{e}", "ERROR")


def get_test_message():
    """測試訊息"""
    taiwan_time = get_taiwan_now()
    return f"""🤖 智能生活助手測試成功！

⏰ 台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}

🔧 功能狀態：
✅ AI 對話功能
✅ 節日提醒系統
✅ 24小時關懷功能
✅ 每日歡迎訊息
✅ 出勤查詢系統
✅ 下班時間提醒

💬 回覆「說明」查看完整功能列表"""


def get_help_message():
    """說明訊息"""
    return """📚 智能生活助手使用說明

🗣️ AI 對話功能：
直接跟我聊天就可以了！我會用溫暖有趣的方式回應你。

📅 節日提醒功能：
• 會自動在重要節日前 7天、5天、3天、1天、當天提醒
• 回覆「節日」查看所有已設定的重要日期

⏰ 時間查詢：
回覆「時間」可查看台灣當前時間

💼 出勤查詢（限特定用戶）：
回覆「出勤」可查詢今日出勤狀態

🔧 其他指令：
• 「測試」- 檢查功能狀態
• 「手動檢查」- 立即檢查節日提醒

💕 特別功能：
• 24小時關懷：超過24小時沒對話會主動關心
• 每日歡迎：每天第一次使用會有特別歡迎訊息
• 智能下班提醒：會在預估下班時間前提醒"""


def get_fallback_response(user_name):
    """備用回應"""
    taiwan_time = get_taiwan_now()

    if user_name == '老婆':
        fallback_messages = [
            f"騷鵝寶貝～雖然我現在回應不太順暢，但我的心永遠和你在一起！💕\n\n台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"親愛的騷鵝，人家現在腦筋有點轉不過來，但還是好想你～🥰\n\n台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        return random.choice(fallback_messages)
    else:
        return f"""抱歉，我現在回應有點遲鈍，但還是很開心能跟你聊天！

你可以試試以下功能：
• 「說明」- 查看功能列表
• 「節日」- 查看重要節日
• 「時間」- 查看台灣時間

台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}"""


# ============== 排程任務設定 ==============

def setup_schedules():
    """設定所有排程任務"""
    safe_print("設定排程任務...", "INFO")

    # 節日檢查 - 每天 9:00, 12:00, 18:00, 21:00
    schedule.every().day.at("09:00").do(check_all_holidays)
    schedule.every().day.at("12:00").do(check_all_holidays)
    schedule.every().day.at("18:00").do(check_all_holidays)
    schedule.every().day.at("21:00").do(check_all_holidays)

    # 24小時關懷檢查 - 每2小時檢查一次
    schedule.every(2).hours.do(check_wife_inactive_and_send_care)

    # 每日出勤查詢 - 平日 09:30
    schedule.every().monday.at("09:30").do(send_daily_attendance)
    schedule.every().tuesday.at("09:30").do(send_daily_attendance)
    schedule.every().wednesday.at("09:30").do(send_daily_attendance)
    schedule.every().thursday.at("09:30").do(send_daily_attendance)
    schedule.every().friday.at("09:30").do(send_daily_attendance)

    # 下班提醒檢查 - 每分鐘檢查一次（在工作日的下午時段）
    schedule.every().minute.do(check_work_end_reminders)

    # 每日清理 - 凌晨 01:00
    schedule.every().day.at("01:00").do(daily_cleanup)

    # 上班提醒 - 平日 08:30
    schedule.every().monday.at("08:30").do(lambda: send_work_reminder("work_start"))
    schedule.every().tuesday.at("08:30").do(lambda: send_work_reminder("work_start"))
    schedule.every().wednesday.at("08:30").do(lambda: send_work_reminder("work_start"))
    schedule.every().thursday.at("08:30").do(lambda: send_work_reminder("work_start"))
    schedule.every().friday.at("08:30").do(lambda: send_work_reminder("work_start"))

    safe_print("所有排程任務設定完成", "INFO")


def check_work_end_reminders():
    """檢查下班提醒（在排程中調用）"""
    taiwan_time = get_taiwan_now()
    # 只在工作日的下午時段檢查
    if taiwan_time.weekday() < 5 and 14 <= taiwan_time.hour <= 19:  # 週一到週五，下午2點到晚上7點
        work_manager.check_work_end_reminders()


def daily_cleanup():
    """每日清理舊記錄"""
    try:
        safe_print("執行每日清理...", "INFO")

        # 清理各種管理器的舊記錄
        reminder_manager.clear_old_reminders()
        welcome_manager.clear_old_records()
        care_manager.clear_old_records()
        work_manager.clear_work_end_records()

        safe_print("每日清理完成", "INFO")

    except Exception as e:
        safe_print(f"每日清理失敗：{e}", "ERROR")


def run_scheduler():
    """在背景執行排程器"""
    safe_print("排程器開始運行", "INFO")

    while True:
        try:
            schedule.run_pending()
            time.sleep(60)  # 每分鐘檢查一次
        except Exception as e:
            safe_print(f"排程器運行錯誤：{e}", "ERROR")
            time.sleep(60)  # 發生錯誤後等待1分鐘再繼續


# ============== 主程式啟動 ==============

if __name__ == "__main__":
    safe_print("=== 智能生活助手啟動中 ===", "INFO")

    # 檢查必要的環境變數
    required_env_vars = [
        'CHANNEL_ACCESS_TOKEN',
        'CHANNEL_SECRET',
        'YOUR_USER_ID',
        'WIFE_USER_ID',
        'FUTAI_USERNAME',
        'FUTAI_PASSWORD'
    ]

    missing_vars = []
    for var in required_env_vars:
        if not os.environ.get(var):
            missing_vars.append(var)

    if missing_vars:
        safe_print(f"缺少環境變數: {', '.join(missing_vars)}", "ERROR")
        safe_print("請設定所有必要的環境變數", "ERROR")
    else:
        safe_print("環境變數檢查完成", "INFO")

    # 顯示當前設定
    taiwan_time = get_taiwan_now()
    safe_print(f"台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S %Z')}", "INFO")
    safe_print(f"連結用戶數: {len(USERS)}", "INFO")
    safe_print(f"設定節日數: {len(IMPORTANT_DATES)}", "INFO")
    safe_print(f"Google AI API: {'已設定' if GOOGLE_AI_API_KEY else '未設定'}", "INFO")

    # 設定排程任務
    setup_schedules()

    # 啟動背景執行緒
    safe_print("啟動背景執行緒...", "INFO")

    # 排程器執行緒
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    safe_print("排程器執行緒已啟動", "INFO")

    # 自我喚醒執行緒
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    safe_print("自我喚醒執行緒已啟動", "INFO")

    # 啟動 Flask 應用
    port = int(os.environ.get('PORT', 5000))
    safe_print(f"=== 智能生活助手啟動完成，監聽 port {port} ===", "INFO")

    # 啟動時執行一次節日檢查
    try:
        check_all_holidays()
        safe_print("啟動時節日檢查完成", "INFO")
    except Exception as e:
        safe_print(f"啟動時節日檢查失敗：{e}", "ERROR")

    app.run(host='0.0.0.0', port=port, debug=False)
