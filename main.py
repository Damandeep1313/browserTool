import os
import base64
import json
import asyncio
import uuid
import subprocess
import shutil
import urllib.parse
import random
import aiohttp
import uvicorn
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from openai import AsyncOpenAI
from playwright.async_api import async_playwright
import cloudinary
import cloudinary.uploader

# -------------------------------------------------
# 1. SETUP ENV & CLOUDINARY
# -------------------------------------------------
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found in .env")

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET'),
    secure=True
)

# -------------------------------------------------
# 2. SETUP APP
# -------------------------------------------------
app = FastAPI()
os.makedirs("scans", exist_ok=True)
app.mount("/scans", StaticFiles(directory="scans"), name="scans")

class AgentRequest(BaseModel):
    prompt: str 

class AgentResponse(BaseModel):
    status: str
    result: str
    video_url: str | None = None 

# -------------------------------------------------
# 3. STEALTH & UTILS
# -------------------------------------------------

async def get_b64_screenshot(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=4000)
    except:
        pass
    
    await asyncio.sleep(1.5) # Buffer for Cloudflare animations
    screenshot_bytes = await page.screenshot()
    return base64.b64encode(screenshot_bytes).decode("utf-8")

async def apply_ultimate_stealth(page):
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
        Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });
        window.chrome = { runtime: {}, app: {} };
    """)

# -------------------------------------------------
# 4. CAPSOLVER INTEGRATION
# -------------------------------------------------

async def solve_with_capsolver(captcha_type, site_key, website_url):
    """Hits the CapSolver API to get the bypass token"""
    if not CAPSOLVER_API_KEY:
        print("⚠️ No CAPSOLVER_API_KEY found!")
        return None

    print(f"🚀 Sending {captcha_type} to CapSolver for {website_url}...")
    
    task_payload = {
        "clientKey": CAPSOLVER_API_KEY,
        "task": {
            "websiteURL": website_url,
            "websiteKey": site_key
        }
    }

    if captcha_type == "turnstile":
        task_payload["task"]["type"] = "AntiCloudflareTask"
    elif captcha_type == "recaptcha":
        task_payload["task"]["type"] = "ReCaptchaV2TaskProxyless"

    async with aiohttp.ClientSession() as session:
        # Create Task
        async with session.post("https://api.capsolver.com/createTask", json=task_payload) as resp:
            data = await resp.json()
            if data.get("errorId") != 0:
                print(f"❌ CapSolver Task Creation Failed: {data}")
                return None
            
            task_id = data.get("taskId")
            print(f"✅ Task created! ID: {task_id}. Waiting for solution (~10s)...")

        # Poll for Result
        for _ in range(30):
            await asyncio.sleep(1.5)
            async with session.post("https://api.capsolver.com/getTaskResult", json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id}) as resp:
                result_data = await resp.json()
                if result_data.get("status") == "ready":
                    print("🎉 CapSolver successfully solved the challenge!")
                    return result_data.get("solution", {}).get("token")
                elif result_data.get("status") == "failed":
                    print("❌ CapSolver failed to solve.")
                    return None

    return None

async def check_and_handle_captcha(page, client, last_b64_image):
    """Detects CAPTCHA, extracts sitekey, calls CapSolver, and injects token"""
    
    # 1. Cloudflare Turnstile Detection
    turnstile_element = page.locator(".cf-turnstile, [id^='cf-'], iframe[src*='challenges.cloudflare.com']").first
    if await turnstile_element.count() > 0:
        print("🚨 CLOUDFLARE TURNSTILE DETECTED")
        
        site_key = await page.evaluate("""() => {
            let el = document.querySelector('.cf-turnstile');
            return el ? el.getAttribute('data-sitekey') : null;
        }""")
        
        if not site_key:
            print("⚠️ Could not extract Turnstile sitekey from DOM.")
            return (True, "Cloudflare Turnstile blocked access - No sitekey found")

        token = await solve_with_capsolver("turnstile", site_key, page.url)
        if token:
            print("💉 Injecting Turnstile token...")
            await page.evaluate(f"""() => {{
                let input = document.querySelector('[name="cf-turnstile-response"]');
                if (input) {{
                    input.value = '{token}';
                    let form = input.closest('form');
                    if(form) form.submit();
                }}
            }}""")
            await page.wait_for_load_state("networkidle", timeout=8000)
            return (False, None)
        return (True, "CapSolver failed to bypass Turnstile")

    # 2. Standard reCAPTCHA Detection
    recaptcha_element = page.locator(".g-recaptcha, iframe[src*='recaptcha/api2']").first
    if await recaptcha_element.count() > 0:
        print("🚨 RECAPTCHA DETECTED")
        
        site_key = await page.evaluate("""() => {
            let el = document.querySelector('.g-recaptcha');
            if (el) return el.getAttribute('data-sitekey');
            
            let iframe = document.querySelector('iframe[src*="recaptcha"]');
            if (iframe) {
                let params = new URLSearchParams(iframe.src.split('?')[1]);
                return params.get('k');
            }
            return null;
        }""")

        if site_key:
            token = await solve_with_capsolver("recaptcha", site_key, page.url)
            if token:
                print("💉 Injecting reCAPTCHA token...")
                await page.evaluate(f"""() => {{
                    document.getElementById("g-recaptcha-response").innerHTML = "{token}";
                    if (typeof ___grecaptcha_cfg !== 'undefined') {{
                        Object.keys(___grecaptcha_cfg.clients).forEach(function(key) {{
                            let client = ___grecaptcha_cfg.clients[key];
                            if(client && client.callback) client.callback('{token}');
                        }});
                    }}
                }}""")
                await asyncio.sleep(4)
                return (False, None)
            return (True, "CapSolver failed to bypass reCAPTCHA")
            
    return (False, None)

# -------------------------------------------------
# 5. VIDEO & ROUTING HELPERS
# -------------------------------------------------

def get_smart_start_url(prompt: str):
    return "https://www.bing.com" if "search" in prompt.lower() else "https://www.google.com"

async def create_and_upload_video(folder_path: str, session_id: str) -> str | None:
    video_path = f"{folder_path}/output.mp4"
    command = ["ffmpeg", "-y", "-framerate", "1", "-i", f"{folder_path}/step_%d.png", "-c:v", "libx264", "-pix_fmt", "yuv420p", video_path]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        upload_result = cloudinary.uploader.upload(video_path, resource_type="video", public_id=f"agent_runs/{session_id}", overwrite=True)
        shutil.rmtree(folder_path)
        return upload_result.get("secure_url")
    except:
        if os.path.exists(folder_path): shutil.rmtree(folder_path)
        return None

# -------------------------------------------------
# 6. API ENDPOINT
# -------------------------------------------------
@app.post("/agent/run", response_model=AgentResponse)
async def run_agent(request: AgentRequest):
    
    session_id = str(uuid.uuid4())[:8]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_name = f"scans/{timestamp}_{session_id}"
    os.makedirs(folder_name, exist_ok=True)
    
    final_message = "Task timed out."
    final_status = "failed"
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    
    consecutive_failures = 0
    last_action = None
    action_repeat_count = 0

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--window-size=1920,1080"
                ]
            ) 
            
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="America/New_York",
                permissions=["geolocation", "notifications"],
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9", "Upgrade-Insecure-Requests": "1"}
            )
            
            page = await context.new_page()
            await apply_ultimate_stealth(page)
            
            print(f"🚀 Starting Task: {request.prompt}")
            await page.goto(get_smart_start_url(request.prompt), wait_until="domcontentloaded")
            await asyncio.sleep(3)

            for step in range(1, 51):
                if len(context.pages) > 0:
                    page = context.pages[-1]
                    await page.bring_to_front()

                last_b64_image = await get_b64_screenshot(page)
                
                with open(f"{folder_name}/step_{step}.png", "wb") as f:
                    f.write(base64.b64decode(last_b64_image))

                should_stop, captcha_reason = await check_and_handle_captcha(page, client, last_b64_image)
                if should_stop:
                    final_message = f"Failed: {captcha_reason}"
                    break

                # Ask GPT-4o what to do next
                system_prompt = (
                    "You are automating a web task. Look at the screenshot.\n"
                    f"GOAL: {request.prompt}\n"
                    "Return 'done' ONLY when fully completed.\n"
                    "Return JSON: {\"action\": \"click\"|\"type\"|\"done\", \"label\": \"element_text\", \"text_to_type\": \"...\", \"reason\": \"...\"}"
                )

                response = await client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{last_b64_image}"}}]}
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=300
                )

                decision = json.loads(response.choices[0].message.content)
                current_action = decision.get('action', 'unknown')
                
                print(f"📍 Step {step}: {current_action} -> {decision.get('reason')}")
                
                if current_action == last_action: action_repeat_count += 1
                else: action_repeat_count = 0; last_action = current_action
                
                if action_repeat_count >= 5:
                    final_message = "Failed: Stuck in action loop."
                    break

                if current_action == 'done':
                    final_message = "Success"
                    final_status = "success"
                    break
                
                action_succeeded = False
                
                try:
                    if current_action == 'click':
                        label = decision.get('label', '')
                        element = page.get_by_role("link", name=label).first
                        if await element.count() == 0: element = page.get_by_role("button", name=label).first
                        if await element.count() == 0: element = page.locator("input, textarea, button").filter(has_text=label).first
                        if await element.count() == 0: element = page.get_by_text(label, exact=False).first

                        if await element.count() > 0:
                            await element.click(timeout=5000, force=True)
                            await asyncio.sleep(2)
                            action_succeeded = True
                        else:
                            print(f"⚠️ Could not find element: {label}")

                    elif current_action == 'type':
                        search_box = page.locator("input[type='text'], input[type='search'], [aria-label='Search']").first
                        await search_box.click(timeout=5000)
                        await search_box.type(decision.get('text_to_type', ''), delay=random.randint(50, 150))
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(4)
                        action_succeeded = True
                
                except Exception as ex:
                    print(f"⚠️ Action error: {ex}")
                
                consecutive_failures = 0 if action_succeeded else consecutive_failures + 1
                if consecutive_failures >= 8:
                    final_message = "Failed: Too many consecutive action errors"
                    break

            await browser.close()
            print("🏁 Generating video proof...")
            video_url = await create_and_upload_video(folder_name, session_id)

            return {"status": final_status, "result": final_message, "video_url": video_url}

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        video_url = await create_and_upload_video(folder_name, session_id)
        return {"status": "error", "result": f"System Error: {str(e)}", "video_url": video_url}

# -------------------------------------------------
# 7. RUN SERVER
# -------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
