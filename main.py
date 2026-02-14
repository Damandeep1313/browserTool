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
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=3000)
    except:
        pass
    
    screenshot_bytes = await page.screenshot()
    return base64.b64encode(screenshot_bytes).decode("utf-8")

# Helper: Enhanced Stealth Injection
async def apply_stealth(page):
    await page.add_init_script("""
        // Remove webdriver flag
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        
        // Mock plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        
        // Mock languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        
        // Chrome runtime
        window.chrome = {
            runtime: {}
        };
        
        // Permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
    """)

# Helper: Smart Captcha Detection
async def check_and_handle_captcha(page, client, last_b64_image):
    """
    Smart captcha detection with dual approach: DOM + Vision
    Returns: (should_stop, reason)
    """
    # Method 1: Check DOM for captcha elements
    captcha_indicators = [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        ".g-recaptcha",
        "#captcha",
        "input[name='captcha']",
        "[class*='captcha']",
        "[id*='captcha']",
        "div[class*='rc-anchor']"  # reCAPTCHA anchor
    ]
    
    for selector in captcha_indicators:
        try:
            element = page.locator(selector).first
            if await element.count() > 0:
                print(f"🚨 CAPTCHA DETECTED (DOM): {selector}")
                return (True, "reCAPTCHA/Captcha detected - automation cannot proceed")
        except:
            continue
    
    # Method 2: Vision check with GPT-4o (more reliable)
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "Look at this screenshot. Is there a CAPTCHA, 'I'm not a robot' checkbox, or verification challenge visible? Answer only YES or NO."
                },
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{last_b64_image}"}}]
                }
            ],
            max_tokens=10
        )
        
        answer = response.choices[0].message.content.strip().upper()
        if "YES" in answer:
            print(f"🚨 CAPTCHA DETECTED (Vision): GPT-4o confirmed")
            return (True, "CAPTCHA/Bot verification detected - cannot be automated")
            
    except Exception as e:
        print(f"⚠️ Vision captcha check failed: {e}")
    
    return (False, None)

# Helper: Try to bypass/close popups
async def attempt_popup_bypass(page):
    """Try common methods to close popups/overlays"""
    try:
        close_selectors = [
            "button:has-text('Close')",
            "button:has-text('No thanks')",
            "button:has-text('Maybe later')",
            "button:has-text('Skip')",
            "button:has-text('Not now')",
            "[aria-label='Close']",
            ".close-button",
            ".modal-close",
            "[data-dismiss='modal']",
            "button.close",
            "[class*='close']"
        ]
        
        for selector in close_selectors:
            try:
                element = page.locator(selector).first
                if await element.count() > 0:
                    await element.click(timeout=2000)
                    await asyncio.sleep(1)
                    print(f"✅ Closed popup using: {selector}")
                    return True
            except:
                continue
        
        # Try pressing Escape key
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
        
        return False
        
    except Exception as e:
        print(f"⚠️ Popup bypass failed: {e}")
        return False

# Helper: Check for Login/Blocking Popups
async def detect_blocking_elements(page, b64_image, client):
    """Use GPT-4o to detect blocking elements"""
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Look at this screenshot. Detect if there are BLOCKING elements:\n"
                        "1. Login/Signup popups or walls\n"
                        "2. 'Verify you are human' messages\n"
                        "3. Cloudflare security checks\n"
                        "4. Age verification popups\n"
                        "5. Cookie consent that blocks content\n\n"
                        "Return JSON:\n"
                        "{\"blocked\": true/false, \"blocker_type\": \"login\"|\"verification\"|\"cookies\"|\"none\", \"reason\": \"brief explanation\"}"
                    )
                },
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}]
                }
            ],
            max_tokens=150,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        return result
        
    except Exception as e:
        print(f"⚠️ Blocker detection failed: {e}")
        return {"blocked": False, "blocker_type": "none", "reason": "detection failed"}

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

# Helper: Analyze Failure
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
    blocker_detected = False

    try:
        async with async_playwright() as p:
            
            # ---------------------------------------------------------
            # BROWSER LAUNCH - Enhanced stealth configuration
            # ---------------------------------------------------------
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-setuid-sandbox",
                    "--disable-accelerated-2d-canvas",
                    "--disable-gpu"
                ]
            ) 
            
            # Context with enhanced fingerprinting
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="America/New_York",
                permissions=["geolocation"],
                geolocation={"latitude": 40.7128, "longitude": -74.0060},
                color_scheme="light",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1"
                }
            )
            # ---------------------------------------------------------
            
            # Start with one page
            page = await context.new_page()
            await apply_stealth(page)

            print(f"🚀 Starting Task: {request.prompt}")
            
            # SMART ROUTING - Avoid Google Search captchas when possible
            prompt_lower = request.prompt.lower()
            
            if "amazon" in prompt_lower:
                await page.goto("https://www.amazon.in", wait_until="domcontentloaded")
            elif "flipkart" in prompt_lower:
                await page.goto("https://www.flipkart.com", wait_until="domcontentloaded")
            elif "youtube" in prompt_lower:
                await page.goto("https://www.youtube.com", wait_until="domcontentloaded")
            elif "myntra" in prompt_lower:
                await page.goto("https://www.myntra.com", wait_until="domcontentloaded")
            elif "book" in prompt_lower and "flight" not in prompt_lower:
                await page.goto("https://www.amazon.in", wait_until="domcontentloaded")
            elif "search" in prompt_lower:
                # Use DuckDuckGo instead of Google (less aggressive bot detection)
                await page.goto("https://www.duckduckgo.com", wait_until="domcontentloaded")
            else:
                # Default to DuckDuckGo
                await page.goto("https://www.duckduckgo.com", wait_until="domcontentloaded")
            
            await page.wait_for_timeout(3000)

            # --- THE LOOP (50 steps) ---
            for step in range(1, 51):
                
                # --- TAB SWITCHING ---
                all_pages = context.pages
                if len(all_pages) > 0:
                    page = all_pages[-1]
                    await page.bring_to_front()
                # -----------------------------------

                last_b64_image = await get_b64_screenshot(page)
                
                img_path = f"{folder_name}/step_{step}.png"
                with open(img_path, "wb") as f:
                    f.write(base64.b64decode(last_b64_image))

                # IMMEDIATE CAPTCHA CHECK (Critical!)
                should_stop, captcha_reason = await check_and_handle_captcha(page, client, last_b64_image)
                if should_stop:
                    print(f"🛑 Stopping due to: {captcha_reason}")
                    final_message = f"Failed: {captcha_reason}"
                    final_status = "failed"
                    blocker_detected = True
                    break

                # CHECK FOR OTHER BLOCKERS every 3 steps
                if step % 3 == 0 or consecutive_failures > 2:
                    print(f"🔍 Checking for blocking popups at step {step}...")
                    blocker_check = await detect_blocking_elements(page, last_b64_image, client)
                    
                    if blocker_check.get("blocked"):
                        blocker_type = blocker_check.get("blocker_type")
                        reason = blocker_check.get("reason")
                        print(f"🚨 BLOCKER DETECTED: {blocker_type} - {reason}")
                        
                        if blocker_type == "login":
                            # Try to bypass login popup
                            print("🔧 Attempting to close login popup...")
                            bypassed = await attempt_popup_bypass(page)
                            if not bypassed:
                                final_message = f"Failed: Login required - {reason}"
                                final_status = "failed"
                                blocker_detected = True
                                break
                            else:
                                print("✅ Login popup closed, continuing...")
                                await page.wait_for_timeout(2000)
                                consecutive_failures = 0
                                continue
                        
                        elif blocker_type == "cookies":
                            # Try to accept cookies
                            print("🔧 Attempting to accept cookies...")
                            bypassed = await attempt_popup_bypass(page)
                            await page.wait_for_timeout(1000)
                            continue

                # Enhanced prompt with captcha awareness
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
                    "5. Only return action='done' when you have FULLY COMPLETED the ENTIRE goal with ALL steps visible.\n"
                    "6. If you see a CAPTCHA, 'I'm not a robot', reCAPTCHA checkbox, or verification page, return action='done' with reason='Blocked by captcha'.\n"
                    "7. If you see a login popup that won't close, return action='done' with reason='Blocked by login requirement'.\n\n"
                    f"Current Step: {step}/50\n"
                )
                
                if consecutive_failures > 3:
                    system_prompt += (
                        f"\nWARNING: You've had {consecutive_failures} consecutive failures. "
                        "If you're stuck on a popup/login that cannot be closed, return action='done' with reason='Cannot proceed - blocking element'. "
                        "If same action keeps failing, try a DIFFERENT approach or element. "
                    )
                
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
                    # Check if done due to blocker
                    reason_lower = reason.lower()
                    if any(word in reason_lower for word in ["captcha", "verification", "blocked", "robot", "login"]):
                        final_message = f"Failed: {reason}"
                        final_status = "failed"
                        blocker_detected = True
                        break
                    
                    # Additional validation: prevent premature completion
                    if step < 4:
                        print(f"⚠️ Agent tried to finish at step {step} (too early). Asking for verification...")
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
                            await element.click(timeout=5000, force=True) 
                            await asyncio.sleep(2)
                            action_succeeded = True
                        else:
                            print(f"⚠️ Could not find element: {label}")

                    elif decision['action'] == 'type':
                        search_box = page.locator("input[type='text'], input[type='search'], [aria-label='Search']").first
                        await search_box.click(timeout=5000)
                        await search_box.type(decision.get('text_to_type', ''), delay=100)
                        await page.keyboard.press("Enter")
                        
                        # Special handling for DuckDuckGo
                        if "duckduckgo" in page.url:
                            await asyncio.sleep(2)
                        # Special handling for Google (if used)
                        elif "google" in page.url:
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
            if final_status == "failed" and last_b64_image and not blocker_detected:
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
