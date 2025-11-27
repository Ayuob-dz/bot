import telebot
import requests
import json
import os
import logging
import sqlite3
import tempfile
import random
import time
import re
import threading
from datetime import datetime, timedelta
from telebot.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    InputFile
)
from concurrent.futures import ThreadPoolExecutor, as_completed

# 🎯 إعداد احترافي للتسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('ai_creator.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 🔧 إعدادات متقدمة
class Config:
    BOT_TOKEN = "7878895137:AAGRGPfCDE2C74tgAj3GEx8Vu-oMXp2OQTY"
    DEEPSEEK_API_KEYS = [
        "sk-a319d7b4929d40d4ab3a3a8720e5f612",
        "sk-1747bcd3ccb94c2593752b32cecd8adb", 
        "sk-455160eb23714ea1b276ec67fbbcd035"
    ]
    DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
    MAX_FILE_SIZE = 45 * 1024 * 1024  # 45MB للسلامة
    REQUEST_TIMEOUT = 60
    MAX_RETRIES = 3
    RATE_LIMIT_PER_USER = 10  # طلبات لكل مستخدم في الساعة

# 🚀 تهيئة البوت مع إعدادات متقدمة
bot = telebot.TeleBot(Config.BOT_TOKEN, parse_mode="HTML")

# 🏗️ نظام إدارة الحالة المتقدم
class StateManager:
    def __init__(self):
        self.user_states = {}
        self.user_projects = {}
        self.rate_limits = {}
        self.api_stats = {}
        self.lock = threading.RLock()
        
    def set_user_state(self, user_id, state_data):
        with self.lock:
            self.user_states[user_id] = {
                **state_data,
                'timestamp': datetime.now(),
                'retry_count': 0
            }
    
    def get_user_state(self, user_id):
        with self.lock:
            return self.user_states.get(user_id)
    
    def clear_user_state(self, user_id):
        with self.lock:
            self.user_states.pop(user_id, None)
    
    def check_rate_limit(self, user_id):
        with self.lock:
            now = datetime.now()
            user_limits = self.rate_limits.get(user_id, [])
            
            # تنظيف الطلبات القديمة
            user_limits = [t for t in user_limits if now - t < timedelta(hours=1)]
            
            if len(user_limits) >= Config.RATE_LIMIT_PER_USER:
                return False
                
            user_limits.append(now)
            self.rate_limits[user_id] = user_limits
            return True

state_manager = StateManager()

# 🗄️ نظام قاعدة البيانات المتقدم
class DatabaseManager:
    def __init__(self):
        self.init_db()
    
    def init_db(self):
        with sqlite3.connect('ai_creator.db') as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                language_code TEXT,
                created_at TEXT,
                last_active TEXT,
                request_count INTEGER DEFAULT 0
            )''')
            
            conn.execute('''CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                project_type TEXT,
                description TEXT,
                requirements TEXT,
                project_data TEXT,
                status TEXT,
                quality_score INTEGER,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )''')
            
            conn.execute('''CREATE TABLE IF NOT EXISTS api_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key TEXT,
                user_id INTEGER,
                endpoint TEXT,
                status_code INTEGER,
                response_time REAL,
                tokens_used INTEGER,
                created_at TEXT
            )''')
            
            conn.execute('''CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                error_type TEXT,
                error_message TEXT,
                stack_trace TEXT,
                created_at TEXT
            )''')
    
    def log_api_usage(self, api_key, user_id, endpoint, status_code, response_time, tokens_used):
        with sqlite3.connect('ai_creator.db') as conn:
            conn.execute('''INSERT INTO api_usage 
                         (api_key, user_id, endpoint, status_code, response_time, tokens_used, created_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                         (api_key, user_id, endpoint, status_code, response_time, tokens_used, 
                          datetime.now().isoformat()))
    
    def log_error(self, user_id, error_type, error_message, stack_trace=None):
        with sqlite3.connect('ai_creator.db') as conn:
            conn.execute('''INSERT INTO error_logs 
                         (user_id, error_type, error_message, stack_trace, created_at)
                         VALUES (?, ?, ?, ?, ?)''',
                         (user_id, error_type, error_message, stack_trace, datetime.now().isoformat()))

db_manager = DatabaseManager()

# 🧠 نظام الذكاء الاصطناعي المتقدم
class AIService:
    def __init__(self):
        self.current_key_index = 0
        self.failed_keys = set()
        self.executor = ThreadPoolExecutor(max_workers=3)
    
    def get_available_key(self):
        """نظام تدوير المفاتيح الذكي"""
        available_keys = [k for k in Config.DEEPSEEK_API_KEYS if k not in self.failed_keys]
        if not available_keys:
            return None
        
        key = available_keys[self.current_key_index % len(available_keys)]
        self.current_key_index += 1
        return key
    
    def validate_description(self, description, project_type):
        """التحقق من جودة الوصف"""
        issues = []
        
        if len(description.strip()) < 10:
            issues.append("الوصف قصير جداً. يرجى تقديم وصف مفصل.")
        
        if len(description) > 2000:
            issues.append("الوصف طويل جداً. يرجى الاختصار مع الحفاظ على الوضوح.")
        
        # التحقق من المحتوى غير المرغوب
        inappropriate_patterns = [
            r'https?://', r'@\w+', r'#\w+'
        ]
        
        for pattern in inappropriate_patterns:
            if re.search(pattern, description, re.IGNORECASE):
                issues.append("الوصف يحتوي على روابط أو إشارات غير مسموحة")
                break
        
        return issues
    
    def enhance_prompt(self, description, project_type, requirements=None):
        """تحسين الprompt للحصول على أفضل النتائج"""
        
        base_system_prompt = """You are an expert full-stack developer and UI/UX designer. 
Create professional, production-ready code with:

ESSENTIAL REQUIREMENTS:
1. MODERN, RESPONSIVE DESIGN
2. CLEAN, MAINTAINABLE CODE
3. PROPER ERROR HANDLING
4. ACCESSIBILITY STANDARDS
5. CROSS-BROWSER COMPATIBILITY
6. PERFORMANCE OPTIMIZATION

TECHNICAL STANDARDS:
- Semantic HTML5
- CSS3 with Flexbox/Grid
- Vanilla JavaScript (ES6+)
- Mobile-first approach
- SEO best practices
- Security considerations

DESIGN PRINCIPLES:
- Clean, modern aesthetics
- Intuitive user experience
- Consistent color scheme
- Proper typography hierarchy
- Smooth animations
- Professional layout

Return ONLY valid JSON with this exact structure:
{
    "html": "complete HTML code with comments",
    "css": "complete CSS with responsive design", 
    "js": "clean JavaScript with error handling",
    "documentation": "brief setup instructions"
}"""

        user_prompt = f"""
PROJECT REQUEST:
{description}

ADDITIONAL REQUIREMENTS:
{requirements or "Standard professional implementation"}

SPECIFIC INSTRUCTIONS:
- Use Arabic language support (dir='rtl', lang='ar')
- Implement modern, professional design
- Include responsive navigation
- Add smooth animations
- Ensure fast loading
- Follow accessibility guidelines
- Use semantic HTML structure
- Include proper error handling
- Optimize for performance
- Add relevant meta tags

Please provide complete, production-ready code.
"""
        
        return base_system_prompt, user_prompt
    
    def generate_project(self, description, project_type, requirements=None, user_id=None):
        """إنشاء المشروع مع معالجة متقدمة للأخطاء"""
        
        # التحقق من جودة الوصف
        validation_issues = self.validate_description(description, project_type)
        if validation_issues:
            raise ValidationError(" | ".join(validation_issues))
        
        # تحسين الprompt
        system_prompt, user_prompt = self.enhance_prompt(description, project_type, requirements)
        
        # المحاولة مع retry logic
        for attempt in range(Config.MAX_RETRIES):
            try:
                api_key = self.get_available_key()
                if not api_key:
                    raise APINotAvailableError("No available API keys")
                
                start_time = time.time()
                
                response = requests.post(
                    Config.DEEPSEEK_API_URL,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}"
                    },
                    json={
                        "model": "deepseek-coder",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 4000,
                        "top_p": 0.9
                    },
                    timeout=Config.REQUEST_TIMEOUT
                )
                
                response_time = time.time() - start_time
                
                # تسجيل استخدام API
                tokens_used = len(description) // 4  # تقدير تقريبي
                db_manager.log_api_usage(
                    api_key[:10] + "***", user_id, "chat/completions", 
                    response.status_code, response_time, tokens_used
                )
                
                if response.status_code == 200:
                    result = response.json()
                    content = result['choices'][0]['message']['content']
                    
                    # استخراج وتحليل JSON
                    project_data = self.extract_and_validate_json(content)
                    
                    # تحسين الجودة النهائية
                    enhanced_data = self.enhance_project_quality(project_data, description)
                    
                    logger.info(f"Project generated successfully for user {user_id}")
                    return enhanced_data
                    
                else:
                    logger.warning(f"API attempt {attempt + 1} failed: {response.status_code}")
                    self.failed_keys.add(api_key)
                    
            except requests.exceptions.Timeout:
                logger.warning(f"API timeout on attempt {attempt + 1}")
                continue
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error on attempt {attempt + 1}: {e}")
                continue
            except Exception as e:
                logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
                continue
        
        raise ProjectGenerationError("Failed to generate project after multiple attempts")
    
    def extract_and_validate_json(self, content):
        """استخراج والتحقق من صحة JSON"""
        try:
            # البحث عن JSON في المحتوى
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if not json_match:
                raise JSONExtractionError("No JSON found in response")
            
            json_str = json_match.group()
            data = json.loads(json_str)
            
            # التحقق من الهيكل الأساسي
            required_keys = ['html', 'css']
            for key in required_keys:
                if key not in data:
                    raise JSONValidationError(f"Missing required key: {key}")
            
            return data
            
        except json.JSONDecodeError as e:
            raise JSONExtractionError(f"Invalid JSON format: {e}")
    
    def enhance_project_quality(self, project_data, description):
        """تحسين جودة المشروع النهائي"""
        
        # تحسين HTML
        if 'html' in project_data:
            html = project_data['html']
            
            # إضافة دعم العربية إذا لم يكن موجوداً
            if 'lang="ar"' not in html:
                html = html.replace('<html>', '<html lang="ar" dir="rtl">')
            
            # إضافة meta tags مهمة
            if '<meta name="viewport"' not in html:
                viewport_meta = '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
                html = html.replace('</head>', f'    {viewport_meta}\n</head>')
            
            project_data['html'] = html
        
        # تحسين CSS
        if 'css' in project_data:
            css = project_data['css']
            
            # إضافة أساسيات التصميم المتجاوب
            if '@media' not in css and 'mobile' not in css.lower():
                responsive_css = '''

/* ===== RESPONSIVE DESIGN ===== */
@media (max-width: 768px) {
    .container {
        padding: 0 15px;
    }
    
    nav ul {
        flex-direction: column;
        gap: 10px;
    }
    
    h1 {
        font-size: 2rem;
    }
}

@media (max-width: 480px) {
    h1 {
        font-size: 1.5rem;
    }
    
    section {
        padding: 40px 0;
    }
}
'''
                css += responsive_css
            
            project_data['css'] = css
        
        # تحسين JavaScript
        if 'js' in project_data:
            js = project_data['js']
            
            # إضافة معالجة الأخطاء إذا لم تكن موجودة
            if 'try' not in js and 'catch' not in js:
                js = f'// Error handling and initialization\ndocument.addEventListener("DOMContentLoaded", function() {{\n    try {{\n{js}\n    }} catch (error) {{\n        console.error("Application error:", error);\n    }}\n}});'
            
            project_data['js'] = js
        
        return project_data

# 🎨 نظام واجهة المستخدم المتقدم
class UIManager:
    @staticmethod
    def create_main_keyboard():
        keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        keyboard.add(
            "🌐 إنشاء موقع ويب", 
            "📱 إنشاء تطبيق",
            "🚀 مشاريعي",
            "📊 إحصائياتي",
            "🛠️ الجودة والتحسين",
            "ℹ️ المساعدة"
        )
        return keyboard
    
    @staticmethod
    def create_project_type_keyboard():
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🛒 موقع تجارة إلكترونية", callback_data="type_ecommerce"),
            InlineKeyboardButton("📊 موقع شركة", callback_data="type_corporate"),
            InlineKeyboardButton("🎓 موقع تعليمي", callback_data="type_educational"),
            InlineKeyboardButton("📝 موقع شخصي", callback_data="type_portfolio"),
            InlineKeyboardButton("🍽️ موقع مطعم", callback_data="type_restaurant"),
            InlineKeyboardButton("⚕️ موقع طبي", callback_data="type_medical")
        )
        return markup
    
    @staticmethod
    def create_quality_options_keyboard():
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("⭐ أساسي", callback_data="quality_basic"),
            InlineKeyboardButton("⭐⭐ متقدم", callback_data="quality_advanced"),
            InlineKeyboardButton("⭐⭐⭐ احترافي", callback_data="quality_pro"),
            InlineKeyboardButton("⭐⭐⭐⭐ ممتاز", callback_data="quality_premium")
        )
        return markup

# 🎯 معالجة الأخطاء المخصصة
class ProjectGenerationError(Exception):
    """خطأ في إنشاء المشروع"""
    pass

class ValidationError(Exception):
    """خطأ في التحقق من البيانات"""
    pass

class APINotAvailableError(Exception):
    """خطأ في توفر API"""
    pass

class JSONExtractionError(Exception):
    """خطأ في استخراج JSON"""
    pass

class JSONValidationError(Exception):
    """خطأ في تحقق JSON"""
    pass

# 🌟 تهيئة الخدمات
ai_service = AIService()
ui_manager = UIManager()

# 💫 نظام التتبع والتحليلات
def track_user_activity(user_id, action, details=None):
    """تتبع نشاط المستخدم"""
    logger.info(f"User {user_id} performed {action}: {details}")

def calculate_quality_score(project_data):
    """حساب درجة جودة المشروع"""
    score = 0
    
    if 'html' in project_data:
        html = project_data['html']
        if 'lang="ar"' in html:
            score += 20
        if 'viewport' in html:
            score += 15
        if 'semantic' in html.lower() or ('<header>' in html and '<footer>' in html):
            score += 25
    
    if 'css' in project_data:
        css = project_data['css']
        if '@media' in css:
            score += 20
        if 'flex' in css or 'grid' in css:
            score += 15
        if 'animation' in css or 'transition' in css:
            score += 10
    
    if 'js' in project_data:
        js = project_data['js']
        if 'addEventListener' in js:
            score += 10
        if 'try' in js and 'catch' in js:
            score += 15
    
    return min(score, 100)

# 🚀 معالجات البوت الأساسية
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    
    track_user_activity(user_id, "start_command")
    
    welcome_text = f"""
🎉 <b>مرحباً {user_name}!</b>

🤖 <b>بوت إنشاء المواقع والتطبيقات بالذكاء الاصطناعي</b>

✨ <b>المميزات المتقدمة:</b>
• 🎯 <code>ذكاء اصطناعي متقدم</code> - DeepSeek AI
• 🏗️ <code>تصميم احترافي</code> - أكواد جاهزة للإنتاج
• 📱 <code>تصميم متجاوب</code> - يعمل على جميع الأجهزة
• ⚡ <code>أداء ممتاز</code> - تحسينات السرعة والأداء
• 🛡️ <code>جودة عالية</code> - معايير احترافية

🚀 <b>لنبدأ رحلتك:</b>
1. اختر نوع المشروع
2. صف ما تريد بدقة
3. اختر مستوى الجودة
4. احصل على مشروعك الاحترافي

🎯 <b>اختر من القائمة:</b>
    """
    
    bot.send_message(
        message.chat.id,
        welcome_text,
        reply_markup=ui_manager.create_main_keyboard(),
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda msg: msg.text == "🌐 إنشاء موقع ويب")
def handle_create_website(message):
    user_id = message.from_user.id
    
    if not state_manager.check_rate_limit(user_id):
        bot.send_message(
            message.chat.id,
            "⏳ <b>تم تجاوز الحد المسموح</b>\n\n"
            "لقد استخدمت الحد الأقصى من الطلبات لهذه الساعة.\n"
            "يرجى المحاولة مرة أخرى لاحقاً.",
            parse_mode="HTML"
        )
        return
    
    track_user_activity(user_id, "start_website_creation")
    
    state_manager.set_user_state(user_id, {
        'action': 'awaiting_project_type',
        'project_category': 'website'
    })
    
    bot.send_message(
        message.chat.id,
        "🌐 <b>مرحلة 1/3: اختر نوع الموقع</b>\n\n"
        "📊 <b>الأنواع المتاحة:</b>\n"
        "• <b>🛒 تجارة إلكترونية</b> - متاجر онлайн متكاملة\n"
        "• <b>📊 موقع شركة</b> - مواقع مؤسسات احترافية\n"  
        "• <b>🎓 تعليمي</b> - منصات تعلم إلكتروني\n"
        "• <b>📝 شخصي</b> - portfolios وسير ذاتية\n"
        "• <b>🍽️ مطعم</b> - قوائم طعام وحجوزات\n"
        "• <b>⚕️ طبي</b> - عيادات وخدمات طبية\n\n"
        "🎯 <b>اختر النوع المناسب:</b>",
        reply_markup=ui_manager.create_project_type_keyboard(),
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('type_'))
def handle_project_type_selection(call):
    user_id = call.from_user.id
    project_type = call.data.replace('type_', '')
    
    type_names = {
        'ecommerce': '🛒 موقع تجارة إلكترونية',
        'corporate': '📊 موقع شركة',
        'educational': '🎓 موقع تعليمي', 
        'portfolio': '📝 موقع شخصي',
        'restaurant': '🍽️ موقع مطعم',
        'medical': '⚕️ موقع طبي'
    }
    
    state_manager.set_user_state(user_id, {
        'action': 'awaiting_description',
        'project_category': 'website',
        'project_type': project_type,
        'type_name': type_names.get(project_type, 'موقع ويب')
    })
    
    bot.edit_message_text(
        f"🎯 <b>مرحلة 2/3: وصف المشروع</b>\n\n"
        f"📝 <b>النوع المحدد:</b> {type_names.get(project_type, 'موقع ويب')}\n\n"
        f"💡 <b>الآن صف مشروعك بالتفصيل:</b>\n"
        f"• الألوان المفضلة\n• الوظائف المطلوبة\n• المحتوى الرئيسي\n• أي متطلبات خاصة\n\n"
        f"📋 <b>مثال احترافي:</b>\n"
        f"<i>\"أريد موقع شركة بمجال التقنية بالألوان الأزرق والأبيض، يحتوي على:\n"
        f"- صفحة رئيسية مع شريط تمرير للميزات\n"
        f"- صفحة عن الشركة مع فريق العمل\n"  
        f"- صفحة خدمات مع تفاصيل كل خدمة\n"
        f"- نموذج اتصال متكامل\n"
        f"- تصميم عصري مع تأثيرات scroll\"</i>\n\n"
        f"🎯 <b>اكتب وصفك الآن:</b>",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda msg: state_manager.get_user_state(msg.from_user.id) and 
                   state_manager.get_user_state(msg.from_user.id)['action'] == 'awaiting_description')
def handle_project_description(message):
    user_id = message.from_user.id
    user_state = state_manager.get_user_state(user_id)
    description = message.text.strip()
    
    try:
        # التحقق من جودة الوصف
        validation_issues = ai_service.validate_description(description, user_state['project_type'])
        if validation_issues:
            error_msg = "\n".join([f"• {issue}" for issue in validation_issues])
            bot.send_message(
                message.chat.id,
                f"⚠️ <b>تحسينات مقترحة للوصف:</b>\n\n{error_msg}\n\n"
                f"📝 <b>يرجى تعديل الوصف وإعادة إرساله:</b>",
                parse_mode="HTML"
            )
            return
        
        # حفظ الوصف والمتابعة لمرحلة الجودة
        user_state['description'] = description
        user_state['action'] = 'awaiting_quality'
        state_manager.set_user_state(user_id, user_state)
        
        track_user_activity(user_id, "project_description_received", 
                          f"type: {user_state['project_type']}, length: {len(description)}")
        
        bot.send_message(
            message.chat.id,
            f"✅ <b>تم استلام الوصف بنجاح!</b>\n\n"
            f"📝 <b>ملخص الطلب:</b>\n"
            f"• <b>النوع:</b> {user_state['type_name']}\n"
            f"• <b>الوصف:</b> {description[:100]}...\n\n"
            f"🎯 <b>مرحلة 3/3: مستوى الجودة</b>\n\n"
            f"⭐ <b>مستويات الجودة:</b>\n"
            f"• <b>أساسي</b> - تصميم بسيط وظيفي\n"
            f"• <b>متقدم</b> - تصميم متجاوب بميزات إضافية\n"
            f"• <b>احترافي</b> - تصميم احترافي مع تأثيرات متقدمة\n"
            f"• <b>ممتاز</b> - أعلى مستوى من الجودة والتفاصيل\n\n"
            f"💎 <b>اختر مستوى الجودة المطلوب:</b>",
            reply_markup=ui_manager.create_quality_options_keyboard(),
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Error processing description for user {user_id}: {e}")
        db_manager.log_error(user_id, "description_processing", str(e))
        
        bot.send_message(
            message.chat.id,
            "❌ <b>حدث خطأ أثناء معالجة الوصف</b>\n\n"
            "يرجى المحاولة مرة أخرى أو الاتصال بالدعم.",
            parse_mode="HTML"
        )

@bot.callback_query_handler(func=lambda call: call.data.startswith('quality_'))
def handle_quality_selection(call):
    user_id = call.from_user.id
    quality_level = call.data.replace('quality_', '')
    user_state = state_manager.get_user_state(user_id)
    
    if not user_state:
        bot.send_message(call.message.chat.id, "❌ انتهت الجلسة. يرجى البدء من جديد.")
        return
    
    quality_names = {
        'basic': '⭐ أساسي',
        'advanced': '⭐⭐ متقدم', 
        'pro': '⭐⭐⭐ احترافي',
        'premium': '⭐⭐⭐⭐ ممتاز'
    }
    
    user_state['quality'] = quality_level
    user_state['quality_name'] = quality_names.get(quality_level, 'أساسي')
    state_manager.set_user_state(user_id, user_state)
    
    # بدء عملية الإنشاء
    bot.edit_message_text(
        f"🚀 <b>بدء الإنشاء...</b>\n\n"
        f"📊 <b>تفاصيل الطلب:</b>\n"
        f"• <b>النوع:</b> {user_state['type_name']}\n"
        f"• <b>الجودة:</b> {quality_names.get(quality_level, 'أساسي')}\n"
        f"• <b>الحالة:</b> جاري المعالجة...\n\n"
        f"⏳ <b>قد تستغرق العملية 1-2 دقائق</b>\n"
        f"🤖 <b>جاري استخدام الذكاء الاصطناعي...</b>",
        call.message.chat.id,
        call.message.message_id
    )
    
    # إنشاء المشروع في thread منفصل
    threading.Thread(
        target=create_project_background,
        args=(user_id, user_state, call.message.chat.id, call.message.message_id)
    ).start()

def create_project_background(user_id, user_state, chat_id, message_id):
    """إنشاء المشروع في الخلفية"""
    try:
        # تحديث حالة التقدم
        progress_messages = [
            "🔍 تحليل المتطلبات...",
            "🎨 تصميم الواجهة...", 
            "⚡ برمجة الوظائف...",
            "📱 تحسين التجربة...",
            "🛠️ مراجعة الجودة..."
        ]
        
        for i, progress_msg in enumerate(progress_messages):
            time.sleep(2)  # محاكاة التقدم
            try:
                bot.edit_message_text(
                    f"🚀 <b>جاري الإنشاء...</b>\n\n"
                    f"📊 <b>التقدم:</b> {(i+1)*20}%\n"
                    f"🔧 <b>المرحلة:</b> {progress_msg}\n\n"
                    f"⏳ <b>يرجى الانتظار...</b>",
                    chat_id,
                    message_id
                )
            except:
                pass  # تجاهل أخطاء تعديل الرسالة
        
        # إنشاء المشروع باستخدام الذكاء الاصطناعي
        project_data = ai_service.generate_project(
            description=user_state['description'],
            project_type=user_state['project_type'],
            requirements=f"جودة: {user_state['quality_name']}",
            user_id=user_id
        )
        
        # حساب درجة الجودة
        quality_score = calculate_quality_score(project_data)
        
        # حفظ المشروع في قاعدة البيانات
        with sqlite3.connect('ai_creator.db') as conn:
            conn.execute('''INSERT INTO projects 
                         (user_id, project_type, description, project_data, status, quality_score, created_at, updated_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                         (user_id, user_state['project_type'], user_state['description'],
                          json.dumps(project_data), 'مكتمل', quality_score,
                          datetime.now().isoformat(), datetime.now().isoformat()))
        
        # إرسال الملفات
        send_project_files(chat_id, project_data, user_state, quality_score)
        
        # تنظيف حالة المستخدم
        state_manager.clear_user_state(user_id)
        
        track_user_activity(user_id, "project_created_successfully", 
                          f"quality: {user_state['quality_name']}, score: {quality_score}")
        
    except ValidationError as e:
        error_msg = str(e)
        bot.edit_message_text(
            f"❌ <b>خطأ في التحقق</b>\n\n{error_msg}\n\n"
            f"📝 يرجى تعديل الوصف وإعادة المحاولة.",
            chat_id, message_id
        )
        db_manager.log_error(user_id, "validation_error", error_msg)
        
    except ProjectGenerationError as e:
        error_msg = str(e)
        bot.edit_message_text(
            f"❌ <b>خطأ في الإنشاء</b>\n\n{error_msg}\n\n"
            f"🔄 يرجى المحاولة مرة أخرى.",
            chat_id, message_id
        )
        db_manager.log_error(user_id, "generation_error", error_msg)
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Unexpected error in project creation: {e}")
        bot.edit_message_text(
            f"❌ <b>خطأ غير متوقع</b>\n\n{error_msg}\n\n"
            f"🛠️ تم تسجيل الخطأ وسيتم معالجته.",
            chat_id, message_id
        )
        db_manager.log_error(user_id, "unexpected_error", error_msg)

def send_project_files(chat_id, project_data, user_state, quality_score):
    """إرسال ملفات المشروع بشكل احترافي"""
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            # إعداد الملفات
            files_to_send = []
            
            if 'html' in project_data:
                html_file = os.path.join(tmp_dir, "index.html")
                with open(html_file, 'w', encoding='utf-8') as f:
                    f.write(project_data['html'])
                files_to_send.append(("📄 index.html", html_file, "الملف الرئيسي للموقع"))
            
            if 'css' in project_data:
                css_file = os.path.join(tmp_dir, "style.css")
                with open(css_file, 'w', encoding='utf-8') as f:
                    f.write(project_data['css'])
                files_to_send.append(("🎨 style.css", css_file, "ملف التنسيق والتصميم"))
            
            if 'js' in project_data:
                js_file = os.path.join(tmp_dir, "script.js")
                with open(js_file, 'w', encoding='utf-8') as f:
                    f.write(project_data['js'])
                files_to_send.append(("⚡ script.js", js_file, "ملف التفاعلات والوظائف"))
            
            # إرسال الملفات
            for file_name, file_path, description in files_to_send:
                with open(file_path, 'rb') as file:
                    bot.send_document(
                        chat_id,
                        file,
                        caption=f"<b>{file_name}</b>\n{description}",
                        parse_mode="HTML"
                    )
                time.sleep(1)  # تجنب rate limiting
            
            # إرسال ملف التعليمات
            readme_content = create_readme_file(user_state, quality_score, project_data)
            readme_file = os.path.join(tmp_dir, "README.md")
            with open(readme_file, 'w', encoding='utf-8') as f:
                f.write(readme_content)
            
            with open(readme_file, 'rb') as file:
                bot.send_document(
                    chat_id,
                    file,
                    caption="📋 <b>دليل الاستخدام والشرح</b>\nتعليمات التشغيل والتفاصيل",
                    parse_mode="HTML"
                )
            
            # رسالة النجاح النهائية
            success_text = f"""
🎉 <b>تم الإنشاء بنجاح!</b>

📊 <b>تفاصيل المشروع:</b>
• <b>النوع:</b> {user_state['type_name']}
• <b>الجودة:</b> {user_state['quality_name']}
• <b>درجة الجودة:</b> {quality_score}/100
• <b>الملفات:</b> {len(files_to_send)} ملف

🚀 <b>خطوات التشغيل:</b>
1. احفظ جميع الملفات في مجلد واحد
2. افتح ملف index.html في المتصفح
3. استمتع بموقعك الجديد!

💡 <b>نصائح مهمة:</b>
• يمكنك تعديل الألوان في ملف style.css
• يمكنك إضافة محتوى جديد في index.html
• الموقع جاهز للتطوير والإضافة

🔧 <b>لإنشاء مشروع جديد:</b>
اختر "إنشاء موقع ويب" من القائمة الرئيسية.
            """
            
            bot.send_message(chat_id, success_text, parse_mode="HTML")
            
        except Exception as e:
            logger.error(f"Error sending files: {e}")
            bot.send_message(
                chat_id,
                f"❌ <b>خطأ في إرسال الملفات</b>\n\n{str(e)}",
                parse_mode="HTML"
            )

def create_readme_file(user_state, quality_score, project_data):
    """إنشاء ملف README احترافي"""
    
    return f"""# 🎯 {user_state['type_name']}

## 📝 الوصف
{user_state['description']}

## 🏆 مواصفات الجودة
- **مستوى الجودة:** {user_state['quality_name']}
- **درجة الجودة:** {quality_score}/100
- **التاريخ:** {datetime.now().strftime('%Y-%m-%d %H:%M')}

## 🚀 خطوات التشغيل
1. احفظ جميع الملفات في مجلد واحد
2. افتح ملف `index.html` في متصفح الويب
3. الموقع جاهز للاستخدام!

## 📁 هيكل الملفات
- `index.html` - الصفحة الرئيسية
- `style.css` - أنماط التصميم
- `script.js` - الوظائف التفاعلية

## 🛠️ إرشادات التطوير
- يمكنك تعديل الألوان في `style.css`
- يمكنك إضافة محتوى جديد في `index.html`
- يمكنك تحسين الوظائف في `script.js`

## 📱 المميزات
- تصميم متجاوب
- دعم اللغة العربية
- كود نظيف ومنظم
- سهولة التعديل والتطوير

## 🤖 المطور
تم إنشاء هذا المشروع باستخدام الذكاء الاصطناعي المتقدم
"""

# 🎯 تشغيل البوت
if __name__ == "__main__":
    logger.info("🚀 Starting Advanced AI Project Creator Bot...")
    logger.info(f"🔑 Available API Keys: {len(Config.DEEPSEEK_API_KEYS)}")
    logger.info("💫 Bot is ready and listening...")
    
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=30)
    except Exception as e:
        logger.critical(f"Bot crashed: {e}")
        raise