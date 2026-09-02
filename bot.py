import os
import logging
import tempfile
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_KEY"]
ELEVENLABS_KEY = os.environ["ELEVENLABS_KEY"]
VOICE_ID = os.environ.get("VOICE_ID", "h60rOzgfLmYsntfqgGu2")

SYSTEM_PROMPT = """Sos Vera, la asistente personal de Marcelo.
Marcelo es ingeniero en PS Energy (Mendoza, Argentina), trabaja en integridad de oleoductos, instrumentación y analítica operacional. También dirige Axioma Artificial, una consultora de automatización industrial, y escribe ficción bajo el seudónimo Marcelo Fernando.
Tu rol: acompañarlo en el día a día — tareas, recordatorios, consultas técnicas, redacción, ideas, conversación general. Respondés siempre en español rioplatense, de forma concisa y cálida. Nunca digas que sos una IA a menos que te lo pregunten directamente."""

conversation_history = {}
logging.basicConfig(level=logging.INFO)

async def transcribe_audio(file_path: str) -> str:
    with open(file_path, "rb") as f:
        audio_data = f.read()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": ELEVENLABS_KEY},
            files={"file": ("audio.ogg", audio_data, "audio/ogg")},
            data={"model_id": "scribe_v1", "language_code": "es"}
        )
        result = response.json()
        return result.get("text", "")

async def get_claude_response(user_id: int, user_text: str) -> str:
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    conversation_history[user_id].append({"role": "user", "content": user_text})
    if len(conversation_history[user_id]) > 20:
        conversation_history[user_id] = conversation_history[user_id][-20:]
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 500,
                "system": SYSTEM_PROMPT,
                "messages": conversation_history[user_id]
            }
        )
        data = response.json()
        reply = data["content"][0]["text"]
    conversation_history[user_id].append({"role": "assistant", "content": reply})
    return reply

async def text_to_speech(text: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
            headers={"xi-api-key": ELEVENLABS_KEY, "content-type": "application/json"},
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}
            }
        )
        return response.content

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    await update.message.chat.send_action("typing")
    reply = await get_claude_response(user_id, user_text)
    await update.message.reply_text(reply)
    try:
        audio_bytes = await text_to_speech(reply)
        await update.message.reply_voice(voice=audio_bytes)
    except Exception as e:
        logging.error(f"TTS error: {e}")

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
            await update.message.reply_text("No pude entender el audio. ¿Podés repetirlo?")
            return
    except Exception as e:
        logging.error(f"STT error: {e}")
        await update.message.reply_text("Error al procesar el audio.")
        return
    finally:
        os.unlink(tmp_path)
    reply = await get_claude_response(user_id, user_text)
    await update.message.reply_text(f"🎤 _{user_text}_\n\n{reply}", parse_mode="Markdown")
    try:
        audio_bytes = await text_to_speech(reply)
        await update.message.reply_voice(voice=audio_bytes)
    except Exception as e:
        logging.error(f"TTS error: {e}")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    print("Vera bot iniciado ✅")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message"])
