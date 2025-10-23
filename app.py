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
from flask import Flask, request, abort, jsonify
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


# ============== 基礎工具函數（必須最先定義） ==============

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
        print(f"[TIME_ERROR] [{level}] {message} (時間格式化錯誤: {e})")


# ============== 🆕 新增：執行鎖定機制 ==============

class ExecutionLock:
    """防止同一任務短時間內重複執行"""

    def __init__(self):
        self.locks = {}
        self.last_execution = {}

    def acquire(self, task_name: str, cooldown_seconds: int = 300) -> bool:
        """
        嘗試取得執行鎖
        task_name: 任務名稱
        cooldown_seconds: 冷卻時間（秒），預設5分鐘
        返回: True 表示可以執行，False 表示需要等待
        """
        current_time = time.time()

        if task_name in self.last_execution:
            elapsed = current_time - self.last_execution[task_name]
            if elapsed < cooldown_seconds:
                safe_print(f"任務 {task_name} 冷卻中，剩餘 {int(cooldown_seconds - elapsed)} 秒", "DEBUG")
                return False

        self.last_execution[task_name] = current_time
        safe_print(f"任務 {task_name} 取得執行鎖", "DEBUG")
        return True

    def reset(self, task_name: str):
        """重置特定任務的鎖"""
        if task_name in self.last_execution:
            del self.last_execution[task_name]
            safe_print(f"已重置任務 {task_name} 的執行鎖", "DEBUG")


# 初始化執行鎖
execution_lock = ExecutionLock()


# ============== 🆕 新增：每日執行記錄管理器 ==============

class DailyExecutionTracker:
    """記錄每日任務執行狀態，確保某些任務一天只執行一次"""

    def __init__(self):
        self.executed_today = {}
        self.current_date = None
        self._update_date()

    def _update_date(self):
        """更新當前日期，如果日期改變則清空記錄"""
        today = get_taiwan_today()
        if self.current_date != today:
            self.current_date = today
            self.executed_today = {}
            safe_print(f"日期更新為 {today}，已清空每日執行記錄", "INFO")

    def mark_executed(self, task_name: str):
        """標記任務已執行"""
        self._update_date()
        self.executed_today[task_name] = get_taiwan_now()
        safe_print(f"標記任務已執行: {task_name}", "DEBUG")

    def is_executed_today(self, task_name: str) -> bool:
        """檢查任務今天是否已執行"""
        self._update_date()
        return task_name in self.executed_today

    def get_execution_time(self, task_name: str):
        """取得任務執行時間"""
        self._update_date()
        return self.executed_today.get(task_name)


# 初始化每日執行追蹤器
daily_tracker = DailyExecutionTracker()


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
            work_end_time = datetime.datetime.strptime(work_end_str, '%H:%M').time()
            today = get_taiwan_today()
            work_end_datetime = datetime.datetime.combine(today, work_end_time)
            work_end_datetime = TAIWAN_TZ.localize(work_end_datetime)

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
        """🆕 改進版：檢查是否需要發送下班提醒（擴大時間視窗）"""
        if not self.daily_work_end_time:
            safe_print("未設定下班時間，跳過下班提醒檢查", "DEBUG")
            return

        try:
            work_end_time = datetime.datetime.strptime(self.daily_work_end_time, '%H:%M').time()
            today = get_taiwan_today()
            work_end_datetime = datetime.datetime.combine(today, work_end_time)
            work_end_datetime = TAIWAN_TZ.localize(work_end_datetime)

            current_time = get_taiwan_now()

            # 🆕 擴大時間視窗：從 2 分鐘改為 10 分鐘
            reminder_configs = [
                {'minutes': 60, 'desc': '1小時前', 'key': '60min', 'window': 600},
                {'minutes': 30, 'desc': '30分鐘前', 'key': '30min', 'window': 600},
                {'minutes': 10, 'desc': '10分鐘前', 'key': '10min', 'window': 600},
                {'minutes': 5, 'desc': '5分鐘前', 'key': '5min', 'window': 600}
            ]

            today_str = today.strftime('%Y-%m-%d')

            for config in reminder_configs:
                reminder_time = work_end_datetime - timedelta(minutes=config['minutes'])
                reminder_id = f"work_end_{config['key']}_{today_str}"

                # 🆕 改進：使用更大的時間視窗（10分鐘）
                time_diff = (current_time - reminder_time).total_seconds()

                if 0 <= time_diff <= config['window'] and reminder_id not in self.work_end_reminders_sent:
                    send_work_end_reminder(config['desc'], self.daily_work_end_time)
                    self.work_end_reminders_sent.add(reminder_id)
                    safe_print(f"✅ 已發送下班提醒：{config['desc']}", "INFO")

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

        f"💕 騷鵝寶貝～我們已經 {hours_since} 小時沒聊天了呢！\n\n灰鵝在鵝窩等你回家等到受不鳥了吶～ 🥺\n記得在外面要注意安全、多喝水唷！"
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
    options.add_argument('--single-process')
    options.add_argument('--memory-pressure-off')
    options.add_argument('--disable-background-timer-throttling')
    options.add_argument('--disable-renderer-backgrounding')
    options.add_argument('--disable-backgrounding-occluded-windows')
    options.add_argument('--window-size=1024,768')
    return options


def click_query_button_improved(driver, wait):
    """改進的查詢按鈕點擊方法"""
    safe_print("尋找並點擊查詢按鈕...", "DEBUG")

    try:
        query_button = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//input[@name='Submit' and @value='查詢']"))
        )

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", query_button)
        time.sleep(1)

        pre_click_html = driver.page_source
        pre_click_hash = hash(pre_click_html)
        safe_print(f"點擊前頁面 hash: {pre_click_hash}", "DEBUG")

        click_success = False

        try:
            query_button.click()
            safe_print("使用普通點擊", "DEBUG")
            click_success = True
        except Exception as e:
            safe_print(f"普通點擊失敗: {e}", "WARNING")

        if not click_success:
            try:
                driver.execute_script("arguments[0].click();", query_button)
                safe_print("使用 JavaScript 點擊", "DEBUG")
                click_success = True
            except Exception as e:
                safe_print(f"JavaScript 點擊失敗: {e}", "WARNING")

        if not click_success:
            try:
                query_button.send_keys(Keys.RETURN)
                safe_print("使用 Enter 鍵觸發", "DEBUG")
                click_success = True
            except Exception as e:
                safe_print(f"Enter 鍵觸發失敗: {e}", "WARNING")

        if not click_success:
            raise Exception("所有點擊方法都失敗了")

        safe_print("等待查詢結果載入...", "DEBUG")

        max_wait_time = 15
        start_time = time.time()

        while time.time() - start_time < max_wait_time:
            time.sleep(1)
            current_html = driver.page_source
            current_hash = hash(current_html)

            if current_hash != pre_click_hash:
                safe_print(f"檢測到頁面內容變化 (等待了 {time.time() - start_time:.1f} 秒)", "DEBUG")
                break

            safe_print(f"等待中... ({time.time() - start_time:.1f}s)", "DEBUG")
        else:
            safe_print("警告: 超時未檢測到頁面變化", "WARNING")

        try:
            WebDriverWait(driver, 5).until_not(
                EC.presence_of_element_located((By.CLASS_NAME, "loading"))
            )
            safe_print("載入指標已消失", "DEBUG")
        except:
            safe_print("沒有找到載入指標，繼續執行", "DEBUG")

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

        today = datetime.datetime.strptime(expected_date, '%Y/%m/%d')
        date_formats = [
            expected_date,
            f"{today.year}/{today.month:02d}/{today.day:02d}",
            f"{today.year}-{today.month:02d}-{today.day:02d}",
        ]

        found_date = False
        for date_format in date_formats:
            if date_format in html_content:
                safe_print(f"找到完整日期: {date_format}", "DEBUG")
                found_date = True
                break

        if not found_date:
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

        try:
            safe_print("重新設定查詢日期...", "DEBUG")
            driver.execute_script(f"document.getElementById('FindDate').value = '{today_str}';")
            driver.execute_script(f"document.getElementById('FindEDate').value = '{today_str}';")

            driver.execute_script(
                "document.getElementById('FindDate').dispatchEvent(new Event('change', {bubbles: true}));"
            )
            driver.execute_script(
                "document.getElementById('FindEDate').dispatchEvent(new Event('change', {bubbles: true}));"
            )

            time.sleep(1)

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

        if click_query_button_improved(driver, wait):
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

        driver.get('https://eportal.futai.com.tw/Home/Login?ReturnUrl=%2F')

        id_field = wait.until(EC.presence_of_element_located((By.ID, 'Account')))
        id_field.send_keys(FUTAI_USERNAME)

        pwd_field = driver.find_element(By.ID, 'Pwd')
        pwd_field.send_keys(FUTAI_PASSWORD)
        pwd_field.submit()

        time.sleep(3)

        driver.get(
            'https://bpmflow.futai.com.tw/futaibpmflow/SignOnFutai.aspx?Account=2993&Token=QxY%2BV82RudxNLWk6ZPWQdiDWxUmcDvnLTJUKvhMIG08%3D&FunctionID=AB-ABS-04')

        time.sleep(3)

        now = get_taiwan_now()
        today_str = f"{now.year}/{now.month}/{now.day}"

        driver.execute_script(f"document.getElementById('FindDate').value = '{today_str}';")
        driver.execute_script(f"document.getElementById('FindEDate').value = '{today_str}';")

        time.sleep(1)

        query_button = driver.find_element(By.XPATH, "//input[@name='Submit' and @value='查詢']")
        driver.execute_script("arguments[0].click();", query_button)

        time.sleep(5)

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

                try:
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
                    work_end = work_start + timedelta(hours=9)
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


def send_daily_attendance_for_husband():
    """發送每日出勤資料給老公（詳細版本，包含下班提醒設定）"""
    safe_print(f"開始執行老公的出勤資料查詢...", "INFO")

    try:
        attendance_data = get_futai_attendance()

        if attendance_data:
            user_attendance = attendance_data.get(FUTAI_USERNAME)

            if user_attendance:
                work_end_str = user_attendance['work_end']
                work_manager.set_work_end_time(work_end_str)
                work_manager.setup_work_end_reminders(work_end_str)

                message = f"""📋 今日出勤資料 ({user_attendance['date']})

👤 {user_attendance['name']} ({FUTAI_USERNAME})
🕐 上班：{user_attendance['work_start']}
🕕 預估下班：{user_attendance['work_end']}

💡 所有刷卡時間：{', '.join(user_attendance['times'])}
⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}

🔔 已設定下班前提醒：1小時、30分鐘、10分鐘、5分鐘"""

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

        line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=message))
        safe_print(f"已發送出勤資料給老公", "INFO")

    except Exception as e:
        safe_print(f"發送老公出勤資料失敗：{e}", "ERROR")
        error_message = f"❌ 出勤查詢過程中發生錯誤，請稍後再試。\n⏰ 時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"
        try:
            line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=error_message))
        except:
            pass


def send_daily_attendance_for_wife():
    """發送灰鵝的出勤資料給騷鵝（溫馨版本，不設定下班提醒）"""
    safe_print(f"開始執行騷鵝的灰鵝出勤資料查詢...", "INFO")

    try:
        attendance_data = get_futai_attendance()

        if attendance_data:
            user_attendance = attendance_data.get(FUTAI_USERNAME)

            if user_attendance:
                message = f"""💕 騷鵝寶貝，查到灰鵝今天的工作狀況囉～

📅 日期：{user_attendance['date']}
🌅 灰鵝上班時間：{user_attendance['work_start']}
🌆 灰鵝預估下班：{user_attendance['work_end']}

💖 你的灰鵝今天準時到公司上班了呢！
在辦公室裡一定也想著在牧場的騷鵝～

🕐 今天總共刷卡：{len(user_attendance['times'])} 次
💝 刷卡時間：{', '.join(user_attendance['times'])}

等灰鵝下班回來就可以陪騷鵝聊天囉！
記得想念你的專屬灰鵝哦～ 💕

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""

            else:
                message = f"""💕 騷鵝寶貝～

灰鵝今天的出勤資料還沒查到呢，可能是：
🤔 灰鵝還沒到公司刷卡(還在鵝窩呼呼大睡)
🤔 系統資料還在更新中
🤔 網路有點小問題

不過不用擔心！你的灰鵝一定會按時上班的～
等等再幫騷鵝查查看！

要是灰鵝敢偷懶不上班，人家就去鵝窩抓他回牧場！😤💕

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""

        else:
            message = f"""💕 騷鵝寶貝～

唉呀！查詢灰鵝出勤資料的時候出了點小狀況，
可能是公司系統在維護中。

但是別擔心哦！你的灰鵝一定在認真工作，
為了我們在牧場的幸福生活努力著呢！💪💕

等等系統好了再幫騷鵝查查看～
有什麼其他想聊的嗎？人家陪你聊天！

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""

        line_bot_api.push_message(WIFE_USER_ID, TextSendMessage(text=message))
        safe_print(f"已發送灰鵝出勤資料給騷鵝", "INFO")

    except Exception as e:
        safe_print(f"發送灰鵝出勤資料給騷鵝失敗：{e}", "ERROR")
        error_message = f"""💕 騷鵝寶貝～

查詢灰鵝出勤的時候出了點小問題，
可能是網路不太穩定。

不過沒關係！等等再試試看，
或者直接問灰鵝本人也可以哦～💕

⏰ 時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""
        try:
            line_bot_api.push_message(WIFE_USER_ID, TextSendMessage(text=error_message))
        except:
            pass


# 🆕 改進版：分離自動排程和手動查詢
def send_daily_attendance_auto():
    """🆕 自動排程專用的出勤查詢（帶防重複機制）"""
    safe_print(f"[自動排程] 開始執行每日出勤資料查詢...", "INFO")

    # 🆕 檢查今天是否已執行過
    if daily_tracker.is_executed_today('daily_attendance'):
        safe_print("[自動排程] 今日已執行過出勤查詢，跳過", "INFO")
        return

    # 🆕 檢查是否在工作日
    taiwan_time = get_taiwan_now()
    if taiwan_time.weekday() >= 5:  # 週六、日
        safe_print("[自動排程] 今天是週末，跳過出勤查詢", "INFO")
        return

    try:
        attendance_data = get_futai_attendance()

        if attendance_data:
            user_attendance = attendance_data.get(FUTAI_USERNAME)

            if user_attendance:
                work_end_str = user_attendance['work_end']
                work_manager.set_work_end_time(work_end_str)
                work_manager.setup_work_end_reminders(work_end_str)

                husband_message = f"""📋 今日出勤資料 ({user_attendance['date']})

👤 {user_attendance['name']} ({FUTAI_USERNAME})
🕐 上班：{user_attendance['work_start']}
🕕 預估下班：{user_attendance['work_end']}

💡 所有刷卡時間：{', '.join(user_attendance['times'])}
⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}

🔔 已設定下班前提醒：1小時、30分鐘、10分鐘、5分鐘"""

                wife_message = f"""💕 騷鵝寶貝，灰鵝的出勤資料來囉～

📅 日期：{user_attendance['date']}
🌅 上班時間：{user_attendance['work_start']}
🌆 預估下班：{user_attendance['work_end']}

💖 你的灰鵝今天也在努力工作，為了我們的未來加油！
騷鵝在外送的時候要注意安全💕騎車不要太快！
記得晚上要誇誇在牧場等你外送回家的灰鵝哦～

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""

            else:
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
            husband_message = f"""❌ 出勤資料查詢失敗

請稍後再試，或手動檢查系統狀態。

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""

            wife_message = f"""💕 騷鵝寶貝～

灰鵝的出勤查詢出了點小問題，可能是系統在維護中。
不過別擔心，你的灰鵝會想辦法處理的！

等等會再試試看的～

⏰ 查詢時間：{get_taiwan_now().strftime('%Y-%m-%d %H:%M:%S')}"""

        try:
            line_bot_api.push_message(YOUR_USER_ID, TextSendMessage(text=husband_message))
            safe_print(f"[自動排程] 已發送出勤資料給老公", "INFO")
        except Exception as e:
            safe_print(f"[自動排程] 發送給老公失敗：{e}", "ERROR")

        try:
            line_bot_api.push_message(WIFE_USER_ID, TextSendMessage(text=wife_message))
            safe_print(f"[自動排程] 已發送出勤資料給騷鵝", "INFO")
        except Exception as e:
            safe_print(f"[自動排程] 發送給騷鵝失敗：{e}", "ERROR")

        # 🆕 標記今日已執行
        daily_tracker.mark_executed('daily_attendance')

    except Exception as e:
        safe_print(f"[自動排程] 執行失敗：{e}", "ERROR")


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
        safe_print(f"✅ 已發送{time_desc}下班提醒", "INFO")
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
        safe_print(f"✅ 提醒訊息發送完成：{holiday_name} - {days_until}天", "INFO")


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


# ============== 🆕 改進版：自我喚醒功能 ==============

def keep_alive():
    """🆕 每 10 分鐘自己戳自己一下，避免 Render 休眠（從 25 分鐘縮短）"""
    app_url = os.environ.get('RENDER_EXTERNAL_URL', 'https://stock-env.onrender.com')

    if not app_url:
        safe_print("未設定 RENDER_EXTERNAL_URL，使用預設值", "WARNING")

    safe_print(f"自我喚醒功能啟動，目標 URL: {app_url}", "INFO")

    while True:
        try:
            # 🆕 改為 10 分鐘（避免 15 分鐘休眠）
            time.sleep(10 * 60)
            response = requests.get(f"{app_url}/health", timeout=10)
            safe_print(f"✅ 自我喚醒完成 - Status: {response.status_code}", "DEBUG")
        except Exception as e:
            safe_print(f"❌ 自我喚醒失敗：{e}", "ERROR")


# ============== 🆕 新增：專用自動化路由（外部觸發） ==============

@app.route("/health", methods=['GET'])
def health_check():
    """🆕 輕量級健康檢查（不執行任何重型任務，快速回應）"""
    taiwan_time = get_taiwan_now()
    return jsonify({
        "status": "healthy",
        "taiwan_time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S'),
        "uptime": "running"
    }), 200


@app.route("/auto/attendance", methods=['GET'])
def auto_attendance():
    """🆕 自動排程專用：每日出勤查詢（帶防重複機制）"""
    # 🆕 檢查執行鎖（5分鐘冷卻）
    if not execution_lock.acquire('attendance', cooldown_seconds=300):
        taiwan_time = get_taiwan_now()
        return jsonify({
            "status": "skipped",
            "reason": "cooldown_active",
            "message": "出勤查詢冷卻中，請稍後再試",
            "time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S')
        }), 200

    try:
        # 在背景執行，避免阻塞 HTTP 回應
        threading.Thread(target=send_daily_attendance_auto, daemon=True).start()

        taiwan_time = get_taiwan_now()
        return jsonify({
            "status": "triggered",
            "message": "出勤查詢已觸發（背景執行中）",
            "time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S')
        }), 200
    except Exception as e:
        safe_print(f"自動出勤查詢觸發失敗：{e}", "ERROR")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route("/auto/work_reminder", methods=['GET'])
def auto_work_reminder():
    """🆕 自動排程專用：下班提醒檢查（每 5 分鐘觸發）"""
    taiwan_time = get_taiwan_now()

    # 只在工作日的 14:00-19:00 執行
    if taiwan_time.weekday() >= 5:
        return jsonify({
            "status": "skipped",
            "reason": "weekend",
            "message": "今天是週末，跳過下班提醒",
            "time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S')
        }), 200

    if not (14 <= taiwan_time.hour < 19):
        return jsonify({
            "status": "skipped",
            "reason": "outside_time_window",
            "message": "不在檢查時間範圍內（14:00-19:00）",
            "time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S')
        }), 200

    try:
        work_manager.check_work_end_reminders()
        return jsonify({
            "status": "checked",
            "message": "下班提醒檢查完成",
            "time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S'),
            "work_end_time": work_manager.daily_work_end_time
        }), 200
    except Exception as e:
        safe_print(f"下班提醒檢查失敗：{e}", "ERROR")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route("/auto/holiday_check", methods=['GET'])
def auto_holiday_check():
    """🆕 自動排程專用：節日檢查"""
    # 檢查執行鎖（30分鐘冷卻）
    if not execution_lock.acquire('holiday_check', cooldown_seconds=1800):
        taiwan_time = get_taiwan_now()
        return jsonify({
            "status": "skipped",
            "reason": "cooldown_active",
            "message": "節日檢查冷卻中",
            "time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S')
        }), 200

    try:
        check_all_holidays()
        taiwan_time = get_taiwan_now()
        return jsonify({
            "status": "completed",
            "message": "節日檢查完成",
            "time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S')
        }), 200
    except Exception as e:
        safe_print(f"節日檢查失敗：{e}", "ERROR")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route("/auto/care_check", methods=['GET'])
def auto_care_check():
    """🆕 自動排程專用：24小時關懷檢查"""
    try:
        check_wife_inactive_and_send_care()
        taiwan_time = get_taiwan_now()
        return jsonify({
            "status": "completed",
            "message": "關懷檢查完成",
            "time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S')
        }), 200
    except Exception as e:
        safe_print(f"關懷檢查失敗：{e}", "ERROR")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route("/auto/daily_cleanup", methods=['GET'])
def auto_daily_cleanup():
    """🆕 自動排程專用：每日清理"""
    try:
        daily_cleanup()
        taiwan_time = get_taiwan_now()
        return jsonify({
            "status": "completed",
            "message": "每日清理完成",
            "time": taiwan_time.strftime('%Y-%m-%d %H:%M:%S')
        }), 200
    except Exception as e:
        safe_print(f"每日清理失敗：{e}", "ERROR")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============== Flask 基本路由 ==============

@app.route("/", methods=['GET'])
def home():
    """主頁（顯示狀態）"""
    taiwan_time = get_taiwan_now()
    return f"""
    🤖 智能生活助手運行中！<br>
    台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}<br>
    架構: 外部觸發模式（UptimeRobot）<br>
    功能: 節日提醒 + AI對話 + 出勤查詢 + 24小時關懷 + 每日歡迎<br>
    狀態: 正常運行<br>
    連結用戶數: {len(USERS)} 位<br>
    <br>
    🔗 <a href="/status">查看詳細狀態</a>
    """


@app.route("/callback", methods=['POST'])
def callback():
    """Line Bot Webhook 回調"""
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
    """手動觸發節日檢查"""
    try:
        check_all_holidays()
        taiwan_time = get_taiwan_now()
        return f"✅ 節日檢查完成 (台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S')})", 200
    except Exception as e:
        safe_print(f"手動檢查錯誤：{e}", "ERROR")
        return f"❌ 檢查失敗：{e}", 500


@app.route("/manual_attendance", methods=['GET'])
def manual_attendance():
    """手動觸發出勤查詢"""
    try:
        threading.Thread(target=send_daily_attendance_auto, daemon=True).start()
        taiwan_time = get_taiwan_now()
        return f"✅ 出勤查詢已觸發（背景執行中） (台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S')})", 200
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
    """🆕 改進版：顯示機器人狀態和時間資訊"""
    taiwan_time = get_taiwan_now()
    utc_time = datetime.datetime.now(datetime.timezone.utc)

    wife_last_time = "從未對話"
    wife_inactive_hours = 0
    if WIFE_USER_ID in care_manager.last_conversation_time:
        wife_last_time = care_manager.last_conversation_time[WIFE_USER_ID].strftime('%Y-%m-%d %H:%M:%S')
        time_diff = taiwan_time - care_manager.last_conversation_time[WIFE_USER_ID]
        wife_inactive_hours = time_diff.total_seconds() / 3600

    status_info = {
        "status": "運行中",
        "architecture": "外部觸發模式（UptimeRobot）",
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
        "work_end_time": work_manager.daily_work_end_time,
        "work_reminders_sent": len(work_manager.work_end_reminders_sent),
        "daily_executed_tasks": list(daily_tracker.executed_today.keys()),
        "features": "節日提醒 + AI對話 + 出勤查詢 + 24小時關懷 + 每日歡迎 + 下班提醒",
    }

    return json.dumps(status_info, ensure_ascii=False, indent=2)


# ============== 每日清理功能 ==============

def daily_cleanup():
    """每日清理舊記錄"""
    try:
        safe_print("執行每日清理...", "INFO")

        reminder_manager.clear_old_reminders()
        welcome_manager.clear_old_records()
        care_manager.clear_old_records()
        work_manager.clear_work_end_records()

        # 🆕 重置每日執行追蹤器（會在 _update_date 時自動清空）
        daily_tracker._update_date()

        safe_print("✅ 每日清理完成", "INFO")

    except Exception as e:
        safe_print(f"每日清理失敗：{e}", "ERROR")


# ============== Line Bot 事件處理 ==============

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """處理 Line Bot 接收到的訊息"""
    user_id = event.source.user_id
    user_message = event.message.text.strip()
    user_name = get_user_name(user_id)

    safe_print(f"收到 {user_name} 的訊息: {user_message}", "INFO")

    care_manager.update_last_conversation_time(user_id)
    check_and_send_daily_welcome(user_id)

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
            threading.Thread(target=send_daily_attendance_for_husband, daemon=True).start()
            reply_text = "📋 正在查詢灰鵝今日出勤資料，請稍候...\n系統將在查詢完成後自動發送結果給您"
            safe_print("📋 老公啟動出勤查詢", "INFO")
        elif user_id == WIFE_USER_ID:
            threading.Thread(target=send_daily_attendance_for_wife, daemon=True).start()
            reply_text = "💕 騷鵝寶貝想知道灰鵝的工作狀況嗎？\n正在幫你查詢灰鵝今天的出勤資料～請稍等一下下哦！"
            safe_print("📋 騷鵝啟動灰鵝出勤查詢", "INFO")
        else:
            threading.Thread(target=send_daily_attendance_for_husband, daemon=True).start()
            reply_text = "📋 正在查詢灰鵝今日出勤資料，請稍候...\n系統將在查詢完成後自動發送結果給您"
            safe_print("📋 其他用戶啟動出勤查詢", "INFO")

    else:
        if should_use_ai_response(user_message):
            ai_response = generate_ai_response(user_message, user_id)
            if ai_response:
                reply_text = ai_response
            else:
                reply_text = get_fallback_response(user_name)
        else:
            reply_text = get_fallback_response(user_name)

    try:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        safe_print(f"✅ 已回覆 {user_name}: {reply_text[:50]}...", "DEBUG")
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
✅ 下班時間提醒（改進版）

🏗️ 架構：外部觸發模式
📡 喚醒間隔：10 分鐘

💬 回覆「說明」查看完整功能列表"""


def get_help_message():
    """🆕 改進版：說明訊息"""
    return """📚 智能生活助手使用說明

🗣️ AI 對話功能：
直接跟我聊天就可以了！我會用溫暖有趣的方式回應你。

📅 節日提醒功能：
• 會自動在重要節日前 7天、5天、3天、1天、當天提醒
• 回覆「節日」查看所有已設定的重要日期

⏰ 時間查詢：
回覆「時間」可查看台灣當前時間

💼 出勤查詢：
回覆「出勤」可查詢今日出勤狀態
• 老公：收到詳細出勤資料 + 下班提醒設定
• 騷鵝：收到溫馨版灰鵝出勤資料

🔧 其他指令：
• 「測試」- 檢查功能狀態
• 「手動檢查」- 立即檢查節日提醒

💕 特別功能：
• 24小時關懷：超過24小時沒對話會主動關心
• 每日歡迎：每天第一次使用會有特別歡迎訊息
• 智能下班提醒：會在預估下班時間前多次提醒（1小時、30分鐘、10分鐘、5分鐘前）

🏗️ 新架構特色：
• 外部觸發模式，不受休眠影響
• 10分鐘自動喚醒，回應更快速
• 防重複執行機制，避免訊息轟炸"""


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
• 「出勤」- 查詢今日出勤

台灣時間：{taiwan_time.strftime('%Y-%m-%d %H:%M:%S')}"""


# ============== 🆕 簡化版排程設定（作為備援） ==============

def setup_schedules():
    """🆕 簡化版排程任務（作為備援，主要依賴外部觸發）"""
    safe_print("設定備援排程任務...", "INFO")

    # 🆕 降低頻率，只作為備援
    schedule.every().day.at("09:00").do(check_all_holidays)
    schedule.every().day.at("18:00").do(check_all_holidays)

    # 每日清理
    schedule.every().day.at("01:00").do(daily_cleanup)

    safe_print("✅ 備援排程任務設定完成（主要依賴外部觸發）", "INFO")


def run_scheduler():
    """在背景執行排程器（備援用）"""
    safe_print("備援排程器開始運行", "INFO")

    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except Exception as e:
            safe_print(f"備援排程器運行錯誤：{e}", "ERROR")
            time.sleep(60)


# ============== 主程式啟動 ==============

if __name__ == "__main__":
    safe_print("=== 🚀 智能生活助手啟動中（外部觸發架構）===", "INFO")

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
        safe_print(f"❌ 缺少環境變數: {', '.join(missing_vars)}", "ERROR")
        safe_print("請設定所有必要的環境變數", "ERROR")
    else:
        safe_print("✅ 環境變數檢查完成", "INFO")

    # 顯示當前設定
    taiwan_time = get_taiwan_now()
    safe_print(f"⏰ 台灣時間: {taiwan_time.strftime('%Y-%m-%d %H:%M:%S %Z')}", "INFO")
    safe_print(f"👥 連結用戶數: {len(USERS)}", "INFO")
    safe_print(f"📅 設定節日數: {len(IMPORTANT_DATES)}", "INFO")
    safe_print(f"🤖 Google AI API: {'已設定' if GOOGLE_AI_API_KEY else '未設定'}", "INFO")
    safe_print(f"🏗️ 架構模式: 外部觸發（UptimeRobot）+ 內部備援", "INFO")

    # 設定備援排程任務
    setup_schedules()

    # 啟動背景執行緒
    safe_print("啟動背景執行緒...", "INFO")

    # 備援排程器執行緒
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    safe_print("✅ 備援排程器執行緒已啟動", "INFO")

    # 🆕 改進的自我喚醒執行緒（10分鐘間隔）
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    safe_print("✅ 自我喚醒執行緒已啟動（10分鐘間隔）", "INFO")

    # 啟動 Flask 應用
    port = int(os.environ.get('PORT', 5000))
    safe_print(f"=== ✅ 智能生活助手啟動完成，監聽 port {port} ===", "INFO")
    safe_print(f"🌐 請在 UptimeRobot 設定以下路由：", "INFO")
    safe_print(f"  • /health - 每 5 分鐘", "INFO")
    safe_print(f"  • /auto/attendance - 09:30, 10:00", "INFO")
    safe_print(f"  • /auto/work_reminder - 每 5 分鐘", "INFO")
    safe_print(f"  • /auto/holiday_check - 09:00, 12:00, 18:00, 21:00", "INFO")
    safe_print(f"  • /auto/care_check - 每 2 小時", "INFO")
    safe_print(f"  • /auto/daily_cleanup - 01:00", "INFO")

    # 啟動時執行一次節日檢查
    try:
        check_all_holidays()
        safe_print("✅ 啟動時節日檢查完成", "INFO")
    except Exception as e:
        safe_print(f"❌ 啟動時節日檢查失敗：{e}", "ERROR")

    app.run(host='0.0.0.0', port=port, debug=False)
