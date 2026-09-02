import os
import logging
import tempfile
import asyncio
import httpx
import edge_tts
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_KEY = os.environ["GROQ_KEY"]
VOICE = "es-AR-ElenaNeural"

SYSTEM_PROMPT = """Sos Vera, la asistente personal de Marcelo.
Marcelo es ingeniero en PS Energy (Mendoza, Argentina), trabaja en integridad de oleoductos,
instrumentacion y analitica operacional. Tambien dirige Axioma Artificial, una consultora de
automatizacion industrial, y escribe ficcion bajo el seudonimo Marcelo Fernando.
Tu rol: acompanarlo en el dia a dia. Respondés siempre en español rioplatense, concisa y calida.
Nunca digas que sos una IA a menos que te lo pregunten directamente."""

conversation_history = {}
logging.basicConfig(level=logging.INFO)


async def transcribe_audio(file_path: str) -> str:
    with open(file_path, "rb") as f:
        audio_data = f.read()
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            files={"file": ("audio.ogg", audio_data, "audio/ogg")},
            data={"model": "whisper-large-v3", "language": "es"}
        )
        return response.json().get("text", "")


async def get_groq_response(user_id: int, user_text: str) -> str:
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    conversation_history[user_id].append({"role": "user", "content": user_text})
    if len(conversation_history[user_id]) > 20:
        conversation_history[user_id] = conversation_history[user_id][-20:]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history[user_id]
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "openai/gpt-oss-120b", "messages": messages, "max_tokens": 500}
        )
        data = response.json()
        reply = data["choices"][0]["message"]["content"]
    conversation_history[user_id].append({"role": "assistant", "content": reply})
    return reply


async def text_to_speech(text: str) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(tmp.name)
    return tmp.name


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    await update.message.chat.send_action("typing")
    try:
        reply = await get_groq_response(user_id, user_text)
        await update.message.reply_text(reply)
        audio_path = await text_to_speech(reply)
        await update.message.reply_voice(voice=open(audio_path, "rb"))
        os.unlink(audio_path)
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text(f"Error: {e}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.chat.send_action("typing")
    voice_file = await update.message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await voice_file.download_to_drive(tmp.name)
        tmp_path = tmp.name
    try:
        user_text = await transcribe_audio(tmp_path)
        if not user_text:
            await update.message.reply_text("No pude entender el audio.")
            return
        reply = await get_groq_response(user_id, user_text)
        await update.message.reply_text(f"🎤 _{user_text}_\n\n{reply}", parse_mode="Markdown")
        audio_path = await text_to_speech(reply)
        await update.message.reply_voice(voice=open(audio_path, "rb"))
        os.unlink(audio_path)
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text(f"Error: {e}")
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    print("Vera bot iniciado")
    app.run_polling(drop_pending_updates=True)
