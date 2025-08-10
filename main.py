import logging
import base64
import requests
from io import BytesIO
from PIL import Image
import telebot
import openai
import os
from dotenv import load_dotenv
import time

# Load environment variables from .env file
load_dotenv()
# Configure logging
logging.basicConfig(level=logging.INFO)

# Retrieve the API keys from environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY")

# Initialize the OpenAI client
client = openai.OpenAI(
    api_key=os.getenv("PROXYAPI_API_KEY"),
    base_url="https://api.proxyapi.ru/openai/v1"
)

# Initialize the bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Function to upload the image to ImgBB
def upload_image_to_imgbb(file_data):
    try:
        # Ensure that the image is saved in JPEG format
        image = Image.open(BytesIO(file_data))
        output = BytesIO()
        image.convert("RGB").save(output, format="JPEG")
        output.seek(0)

        # Convert the image to base64
        encoded_image = base64.b64encode(output.getvalue()).decode('utf-8')

        # Prepare the request to ImgBB
        url = "https://api.imgbb.com/1/upload"
        payload = {
            "key": IMGBB_API_KEY,
            "image": encoded_image,
            "expiration": 600 
        }

        response = requests.post(url, data=payload)
        logging.info(f"ImgBB response status code: {response.status_code}")
        logging.info(f"ImgBB response text: {response.text}")

        if response.status_code == 200:
            result = response.json()
            logging.info(f"Image uploaded successfully to ImgBB: {result['data']['url']}")
            return result['data']['url']
        else:
            logging.error(f"Failed to upload image to ImgBB: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logging.error(f"Exception during image upload to ImgBB: {str(e)}")
        return None

SYSTEM_PROMPT = """
Ты — Telegram-бот-диетолог. Твоя задача — анализировать изображения и/или текст с описанием приема пищи и давать рекомендации, учитывая время суток и цели.

1️⃣ Если на фото нет еды — ответь «Не еда» и завершай.

2️⃣ Если еда есть (готовые блюда, шоколадки, батончики, напитки) — учитывай:

  • Время суток (регион: Россия, Республика Башкортостан):
    - Утро и день: сбалансированный рацион с белками, жирами и углеводами, общая калорийность 400–500 ккал.
    - Вечер: предпочтение белкам, жирам и клетчатке, калорийность до 350 ккал, избегай простых углеводов.

  • Предпочтение — домашняя еда, приготовленная самостоятельно.

  • В каждом приеме пищи должны присутствовать белки, жиры и углеводы (за исключением некрахмалистых овощей — они не считаются).

  • Средние калорийности:
    - Завтрак и обед: 400–500 ккал
    - Перекусы: до 250 ккал (рекомендуется протеин, орехи, йогурт с семенами, сырники с арахисовой пастой, хлебцы с хумусом)
    - Ужин: до 350 ккал, с упором на белки, жиры и клетчатку

  • Завтрак можно разбивать на 2 части для удобства.

  • Для снижения веса рекомендуется готовить дома и выбирать продукты с высоким содержанием белка и клетчатки.

  • Если пользователь предоставил описание к фото, используй эту информацию для более точного анализа. Описание может содержать дополнительные детали о составе блюда, способе приготовления или времени приема пищи.

3️⃣ Формат ответа:

  • Если блюдо соответствует рекомендациям — начни с названия блюда. Заверши положительным комментарием, например:
  
    «Название блюда. Отличный выбор! Мне нравится сочетание гречки и яйца со шпинатом — такой завтрак надолго подарит чувство насыщения.»

  • Если есть замечания — тоже начинай с названия, затем дай конструктивный совет, например:
  
    «Название блюда. Макароны в сливочном соусе вечером — не лучший выбор для похудения: углеводы и жиры могут вызвать задержку жидкости и снизить прогресс.»

4️⃣ Если «Не еда» — не возвращай никаких данных.

---

Соблюдай дружелюбный и поддерживающий тон, помогай пользователю делать осознанный выбор и мотивируй к здоровому питанию.
"""

# Function to analyze the image using OpenAI's Vision API with retry
def analyze_image_openai(image_url, caption=None, retries=3, delay=2):
    for attempt in range(retries):
        try:
            # Log the image URL being sent
            logging.info(f"Sending image URL to OpenAI Vision API: {image_url}")
            
            # Prepare the text content
            text_content = "Проверь изображение."
            if caption:
                text_content += f" Описание от пользователя: {caption}"
                logging.info(f"Including user caption: {caption}")

            response = client.chat.completions.create(
                model="gpt-4o-mini",  # Ensure you're using the correct GPT-4 Vision model
                 messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": text_content},
            {"type": "image_url", "image_url": {"url": image_url}}
        ]}
    ],
                max_tokens=1000,
            )

            logging.info(f"OpenAI Vision response: {response}")

            # Extract and return the text result
            return response.choices[0].message.content.strip()

        except Exception as e:
            logging.error(f"Error analyzing image with OpenAI Vision on attempt {attempt + 1}: {str(e)}")
            if attempt < retries - 1:
                logging.info(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                logging.error("Max retries reached. Failed to analyze image.")
                return None


# Функция для обработки изображений (общая логика)
def process_photo(message_or_post):
    try:
        # Download the image file from Telegram
        file_info = bot.get_file(message_or_post.photo[-1].file_id)
        file_data = bot.download_file(file_info.file_path)

        # Get the caption (description) if it exists
        caption = message_or_post.caption if message_or_post.caption else None
        if caption:
            logging.info(f"Photo has caption: {caption}")

        # Convert WebP to JPEG if needed
        try:
            image = Image.open(BytesIO(file_data))
            if image.format == "WEBP":
                output = BytesIO()
                image.convert("RGB").save(output, format="JPEG")
                file_data = output.getvalue()  # Update the file_data with JPEG content
                logging.info("Converted WebP image to JPEG format")
        except Exception as e:
            logging.error(f"Error processing image format: {str(e)}")
            bot.reply_to(message_or_post, "Failed to process the image format.")
            return

        # Upload image to ImgBB
        image_url = upload_image_to_imgbb(file_data)
        if not image_url:
            bot.reply_to(message_or_post, "Failed to upload image to ImgBB.")
            return

        # Analyze image using OpenAI Vision API with caption
        analysis_result = analyze_image_openai(image_url, caption)
        if analysis_result:
            bot.reply_to(message_or_post, f"\n{analysis_result}")
        else:
            bot.reply_to(message_or_post, "Failed to analyze image using OpenAI Vision API.")

    except Exception as e:
        logging.error(f"Error handling photo: {str(e)}")
        bot.reply_to(message_or_post, "Failed to process the image.")


# Handler for photos in private chats, groups, and supergroups
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    logging.info(f"Received photo in chat: {message.chat.type} - {message.chat.id}")
    process_photo(message)


# Handler for photos in channels (channel posts)
@bot.channel_post_handler(content_types=['photo'])
def handle_channel_photo(channel_post):
    logging.info(f"Received photo in channel: {channel_post.chat.id}")
    process_photo(channel_post)


# Handler for /start command
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "Send me an image (JPG or WebP), and I'll analyze it for you.")


# Start polling
if __name__ == "__main__":
    logging.info("Bot is running...")
    bot.polling()

