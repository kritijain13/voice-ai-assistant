# Voice AI Assistant

Speak into the browser microphone. ElevenLabs transcribes the recording, Groq generates a reply in the same language, and the page displays both as text. The browser automatically reads each answer aloud using its built-in speech synthesis; use **Play Answer** to hear it again.

## Configure

Open PowerShell in this project folder and run:

```powershell
Copy-Item .env.example .env
notepad .env
```

Put your own ElevenLabs and Groq keys in `.env`. Keep that file private; it is excluded by `.gitignore`.

## Start this project

In PowerShell, change to this exact folder, then run:

```powershell
cd "$env:USERPROFILE\Downloads\voice_ai_assistant (3)\voice_ai_assistant"
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8010
```

Open <http://127.0.0.1:8010>. This project uses port **8010** so it can run alongside an older app that may already be using port 8000. Keep the PowerShell window open while using the assistant. Stop it with `Ctrl+C`.

If PowerShell says the virtual environment does not exist, create it once with `python -m venv venv`, then activate it and install the requirements.

## How it works

Browser microphone → this project's FastAPI server → ElevenLabs Speech-to-Text → Groq chat model → transcript and answer shown in the page and spoken by the browser.

The assistant records one clip at a time; press **Stop & Get Answer** to submit it. Recordings over 20 MB are rejected. This is not continuous live streaming. Microphone access works on localhost or an HTTPS site.
