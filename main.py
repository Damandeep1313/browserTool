import os
import base64
import json
import asyncio
import uuid
import subprocess
import shutil
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

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found in .env")

# Configure Cloudinary
cloudinary.config(
  cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME'),
  api_key = os.getenv('CLOUDINARY_API_KEY'),
  api_secret = os.getenv('CLOUDINARY_API_SECRET'),
  secure = True
)

# -------------------------------------------------
# 2. SETUP APP
# -------------------------------------------------
app = FastAPI()
os.makedirs("scans", exist_ok=True)
app.mount("/scans", StaticFiles(directory="scans"), name="scans")

# -------------------------------------------------
# 3. MODELS
# -------------------------------------------------
class AgentRequest(BaseModel):
    prompt: str 

class AgentResponse(BaseModel):
    status: str
    result: str
    video_url: str | None = None 

# -------------------------------------------------
# 4. HELPERS
# -------------------------------------------------

# Helper: Get Screenshot as Base64
async def get_b64_screenshot(page):
    # Ensure page is fully loaded before snapping
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=3000)
    except:
        pass # If timeout, just take the screenshot anyway
    
    screenshot_bytes = await page.screenshot()
    return base64.b64encode(screenshot_bytes).decode("utf-8")

# Helper: Manual Stealth Injection
async def apply_stealth(page):
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """)

# Helper: Video Creation & Upload & Cleanup
async def create_and_upload_video(folder_path: str, session_id: str) -> str | None:
    video_path = f"{folder_path}/output.mp4"
    command = [
        "ffmpeg", "-y", "-framerate", "1", 
        "-i", f"{folder_path}/step_%d.png",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", video_path
    ]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        upload_result = cloudinary.uploader.upload(
            video_path, resource_type = "video",
            public_id = f"agent_runs/{session_id}", overwrite = True
        )
        shutil.rmtree(folder_path)
        return upload_result.get("secure_url")
    except Exception as e:
        print(f"❌ Video Processing failed: {e}")
        if os.path.exists(folder_path):
             shutil.rmtree(folder_path)
        return None

# Helper: Analyze Failure (Updated to detect Login/Popups)
async def analyze_failure(client, prompt, b64_image):
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are a debugger. The agent failed to complete the task: '" + prompt + "'. Look at the screenshot carefully. Is there a Login Popup? Is there a Captcha? Is the item out of stock? Explain the BLOCKER in 1 sentence."
                },
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}]}
            ],
            max_tokens=100
        )
        return response.choices[0].message.content
    except:
        return "Unknown error (could not analyze)."

# -------------------------------------------------
# 5. API ENDPOINT
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
    
    last_b64_image = None 
    
    # Track repeated failures to avoid endless loops
    consecutive_failures = 0
    last_action = None
    action_repeat_count = 0

    try:
        async with async_playwright() as p:
            
            # ---------------------------------------------------------
            # SMART LAUNCH CONFIGURATION
            # ---------------------------------------------------------
            # Check if running on Render (Production) or Local
            is_production = os.getenv("RENDER") is not None
            
            # Launch Args
            launch_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            if not is_production:
                launch_args.append("--start-maximized") # Only maximize window if we have a screen (Local)

            browser = await p.chromium.launch(
                headless=is_production, # True on Render, False on Local
                args=launch_args
            ) 
            
            # Context Configuration
            if is_production:
                # On Server: Force 1080p resolution for clear video
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
                )
            else:
                # On Local: Use full screen
                context = await browser.new_context(
                    no_viewport=True, 
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
                )
            # ---------------------------------------------------------
            
            # Start with one page
            page = await context.new_page()
            await apply_stealth(page)

            print(f"🚀 Starting Task: {request.prompt}")
            
            if "amazon" in request.prompt.lower():
                 await page.goto("https://www.amazon.in")
            elif "google" in request.prompt.lower():
                 await page.goto("https://www.google.com")
            else:
                 await page.goto("https://www.google.com")
            
            await page.wait_for_timeout(3000)

            # --- THE LOOP (Increased to 50 steps) ---
            for step in range(1, 51):
                
                # --- CRITICAL FIX: TAB SWITCHING ---
                # Check if a new tab (page) has opened. If so, switch to it.
                all_pages = context.pages
                if len(all_pages) > 0:
                    # Always focus on the latest tab
                    page = all_pages[-1]
                    await page.bring_to_front()
                # -----------------------------------

                last_b64_image = await get_b64_screenshot(page)
                
                img_path = f"{folder_name}/step_{step}.png"
                with open(img_path, "wb") as f:
                    f.write(base64.b64decode(last_b64_image))

                # Enhanced prompt with task decomposition and strict completion rules
                system_prompt = (
                    "You are a human web user automating a task. Look at the screenshot carefully.\n\n"
                    f"FULL GOAL: {request.prompt}\n\n"
                    "CRITICAL RULES:\n"
                    "1. Break the goal into ALL required steps. Complete EVERY step before returning 'done'.\n"
                    "2. For example, if goal is 'search X, add to cart, checkout':\n"
                    "   - Step A: Search for X on the website\n"
                    "   - Step B: Click on the correct product\n"
                    "   - Step C: Click 'Add to Cart' button\n"
                    "   - Step D: Click 'Proceed to Checkout' or 'Go to Cart'\n"
                    "   - ONLY THEN return action='done'\n"
                    "3. Never assume a step is complete unless you can SEE confirmation on screen.\n"
                    "4. If you see 'Added to Cart' message or cart icon updated, that's ONE step done, but continue to next step.\n"
                    "5. Only return action='done' when you have FULLY COMPLETED the ENTIRE goal with ALL steps visible.\n\n"
                    f"Current Step: {step}/50\n"
                )
                
                # If stuck in a loop, give additional guidance
                if consecutive_failures > 3:
                    system_prompt += (
                        f"\nWARNING: You've had {consecutive_failures} consecutive failures. "
                        "If you're stuck on a popup/login that cannot be closed, return action='done' with reason='Cannot proceed - blocking element'. "
                        "If same action keeps failing, try a DIFFERENT approach or element. "
                    )
                
                # If finishing too early, warn about it
                if step < 5:
                    system_prompt += (
                        f"\nNOTE: You are only at step {step}. Most tasks require 5-15 steps. "
                        "Make sure you've completed ALL parts of the goal before marking 'done'. "
                    )
                
                system_prompt += (
                    "\n\nReturn JSON ONLY in this exact format:\n"
                    "{\"action\": \"click\"|\"type\"|\"done\", \"label\": \"visible_text_on_button_or_link\", \"text_to_type\": \"...\", \"reason\": \"what_you_are_doing_and_why\"}"
                )

                # Ask GPT-4o
                response = await client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "system",
                            "content": system_prompt
                        },
                        {
                            "role": "user",
                            "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{last_b64_image}"}}]
                        }
                    ],
                    max_tokens=300,
                    response_format={"type": "json_object"}
                )

                decision = json.loads(response.choices[0].message.content)
                
                # Safely get action and reason
                current_action = decision.get('action', 'unknown')
                reason = decision.get('reason', 'No reason provided')
                
                print(f"📍 Step {step} (Tab {len(all_pages)}): {current_action} -> {reason}")
                
                # Track if same action repeating
                if current_action == last_action:
                    action_repeat_count += 1
                else:
                    action_repeat_count = 0
                    last_action = current_action
                
                # If same action repeated 5+ times, force stop
                if action_repeat_count >= 5:
                    print(f"🛑 Same action '{current_action}' repeated {action_repeat_count} times. Giving up.")
                    final_message = f"Failed: Stuck in loop - action '{current_action}' repeated {action_repeat_count} times"
                    break

                if decision['action'] == 'done':
                    # Additional validation: prevent premature completion
                    if step < 4:
                        print(f"⚠️ Agent tried to finish at step {step} (too early). Asking for verification...")
                        # Ask AI to confirm if task is REALLY complete
                        verify_response = await client.chat.completions.create(
                            model="gpt-4o",
                            messages=[
                                {
                                    "role": "system",
                                    "content": f"Look at this screenshot. The task was: '{request.prompt}'. Is this task COMPLETELY finished with ALL steps done? Answer only 'YES' or 'NO' with 1 sentence explanation."
                                },
                                {
                                    "role": "user",
                                    "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{last_b64_image}"}}]
                                }
                            ],
                            max_tokens=100
                        )
                        verification = verify_response.choices[0].message.content
                        print(f"🔍 Verification: {verification}")
                        
                        if "NO" in verification.upper():
                            print(f"❌ Verification failed. Continuing task...")
                            consecutive_failures += 1
                            continue
                    
                    final_message = f"Success: {reason}"
                    final_status = "success"
                    break
                
                action_succeeded = False
                
                try:
                    if decision['action'] == 'click':
                        label = decision.get('label', '')
                        
                        # Smart Click Hierarchy
                        element = page.get_by_role("link", name=label).first
                        if await element.count() == 0:
                            element = page.get_by_role("button", name=label).first
                        if await element.count() == 0:
                            element = page.locator("input, textarea, button").filter(has_text=label).first
                        if await element.count() == 0:
                            element = page.get_by_text(label, exact=False).first

                        if await element.count() > 0:
                            await element.hover()
                            await asyncio.sleep(0.5)
                            # Force click incase it's a new tab link
                            await element.click(timeout=5000, force=True) 
                            
                            # Wait slightly longer after clicks to allow new tabs to spawn
                            await asyncio.sleep(2)
                            action_succeeded = True
                        else:
                            print(f"⚠️ Could not find element: {label}")

                    elif decision['action'] == 'type':
                        search_box = page.locator("input[type='text'], input[type='search'], [aria-label='Search']").first
                        await search_box.click(timeout=5000)
                        await search_box.type(decision.get('text_to_type', ''), delay=100)
                        await page.keyboard.press("Enter")
                        if "google" in page.url:
                            await asyncio.sleep(1)
                            await page.keyboard.press("Enter")
                        await page.wait_for_timeout(4000)
                        action_succeeded = True
                
                except Exception as ex:
                    print(f"⚠️ Action error: {ex}")
                    action_succeeded = False
                
                # Track consecutive failures
                if action_succeeded:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                
                # If 8+ consecutive failures, stop
                if consecutive_failures >= 8:
                    print(f"🛑 {consecutive_failures} consecutive failures. Stopping.")
                    final_message = f"Failed: Too many consecutive failures ({consecutive_failures})"
                    break
            
            # --- ERROR ANALYSIS ---
            if final_status == "failed" and last_b64_image:
                print("🤔 Task failed. Analyzing final screenshot...")
                error_reason = await analyze_failure(client, request.prompt, last_b64_image)
                final_message = f"Failed: {error_reason}"

            await browser.close()
            
            # --- VIDEO ---
            print("🏁 Generating video proof...")
            video_url = await create_and_upload_video(folder_name, session_id)
            print(f"✅ Video URL: {video_url}") 

            return {
                "status": final_status,
                "result": final_message,
                "video_url": video_url 
            }

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        video_url = await create_and_upload_video(folder_name, session_id)
        return {
            "status": "error",
            "result": f"System Error: {str(e)}",
            "video_url": video_url
        }

# Run with: python -m uvicorn main:app --reload