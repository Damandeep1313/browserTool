import os
import base64
import json
import asyncio
import uuid
import subprocess
import shutil
import urllib.parse
import random
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

async def get_b64_screenshot(page):
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=3000)
    except:
        pass
    
    screenshot_bytes = await page.screenshot()
    return base64.b64encode(screenshot_bytes).decode("utf-8")

async def apply_ultimate_stealth(page):
    """Maximum stealth - harder to detect"""
    await page.add_init_script("""
        // Remove webdriver
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        
        // Mock plugins with realistic data
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                {
                    0: {type: "application/pdf", suffixes: "pdf", description: "Portable Document Format"},
                    description: "Portable Document Format",
                    filename: "internal-pdf-viewer",
                    length: 1,
                    name: "Chrome PDF Plugin"
                },
                {
                    0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"},
                    description: "Portable Document Format", 
                    filename: "internal-pdf-viewer",
                    length: 1,
                    name: "Chrome PDF Viewer"
                }
            ]
        });
        
        // Mock languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        
        // Chrome runtime
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };
        
        // Permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        
        // Add realistic properties
        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32'
        });
        
        Object.defineProperty(navigator, 'vendor', {
            get: () => 'Google Inc.'
        });
        
        // Mock hardware concurrency
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => 8
        });
    """)

# --- NEW: Human-like mouse movement ---
async def human_like_mouse_move(page, dest_x, dest_y):
    """Simulates a human moving the mouse with jitter and variable speed."""
    # Start from a random position on screen
    start_x = random.randint(100, 800)
    start_y = random.randint(100, 600)
    
    await page.mouse.move(start_x, start_y)
    await asyncio.sleep(random.uniform(0.1, 0.3))

    steps = random.randint(15, 30)
    for i in range(steps):
        t = i / steps
        # Ease in-out formula
        ease_t = t * t * (3 - 2 * t)
        
        # Add human hand jitter
        jitter_x = random.uniform(-3, 3)
        jitter_y = random.uniform(-3, 3)
        
        curr_x = start_x + (dest_x - start_x) * ease_t + jitter_x
        curr_y = start_y + (dest_y - start_y) * ease_t + jitter_y
        
        await page.mouse.move(curr_x, curr_y)
        await asyncio.sleep(random.uniform(0.01, 0.04))
        
    # Final precise move to the exact target
    await page.mouse.move(dest_x, dest_y)

def get_smart_start_url(prompt: str):
    prompt_lower = prompt.lower()
    if "amazon" in prompt_lower: return "https://www.amazon.in"
    elif "flipkart" in prompt_lower: return "https://www.flipkart.com"
    elif "youtube" in prompt_lower: return "https://www.youtube.com"
    elif "myntra" in prompt_lower: return "https://www.myntra.com"
    elif "swiggy" in prompt_lower: return "https://www.swiggy.com"
    elif "zomato" in prompt_lower: return "https://www.zomato.com"
    return "https://www.bing.com"

async def smart_bing_search(page, query: str):
    try:
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://www.bing.com/search?q={encoded_query}"
        print(f"🎯 Bing search URL: {search_url}")
        
        await page.goto(search_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        return True
    except Exception as e:
        print(f"⚠️ Bing search failed: {e}")
        return False

# --- UPDATED: Humanized reCAPTCHA Clicker ---
async def try_click_recaptcha(page):
    """Attempt to click the 'I'm not a robot' checkbox like a human"""
    try:
        print("🤖 Attempting to click reCAPTCHA checkbox (Human Mode)...")
        
        recaptcha_frame = None
        for frame in page.frames:
            if 'recaptcha' in frame.url and 'anchor' in frame.url:
                recaptcha_frame = frame
                print(f"✅ Found reCAPTCHA iframe: {frame.url}")
                break
        
        if not recaptcha_frame:
            return (False, False)
        
        checkbox_selectors = [
            ".recaptcha-checkbox-border",
            "#recaptcha-anchor",
            ".rc-anchor-center-item"
        ]
        
        for selector in checkbox_selectors:
            try:
                checkbox = recaptcha_frame.locator(selector).first
                if await checkbox.count() > 0:
                    print(f"✅ Found checkbox! Simulating human movement...")
                    
                    # Scroll into view just in case
                    await checkbox.scroll_into_view_if_needed()
                    await asyncio.sleep(0.5)
                    
                    # 1. Get exact coordinates
                    box = await checkbox.bounding_box()
                    if not box:
                        continue
                    
                    # 2. Calculate a random click point inside the checkbox
                    target_x = box['x'] + (box['width'] / 2) + random.uniform(-4, 4)
                    target_y = box['y'] + (box['height'] / 2) + random.uniform(-4, 4)
                    
                    # 3. Move the mouse naturally
                    await human_like_mouse_move(page, target_x, target_y)
                    
                    # 4. Human-like pause before clicking
                    await asyncio.sleep(random.uniform(0.3, 0.7))
                    
                    # 5. Click using mouse down/up to mimic physical click
                    await page.mouse.down()
                    await asyncio.sleep(random.uniform(0.08, 0.2))
                    await page.mouse.up()
                    
                    print("✅ Clicked reCAPTCHA checkbox!")
                    
                    # Wait and check if image challenge appeared
                    await asyncio.sleep(random.uniform(3.0, 4.5))
                    
                    for f in page.frames:
                        if 'recaptcha' in f.url and 'bframe' in f.url:
                            print("⚠️ Image challenge appeared - cannot solve automatically yet")
                            return (True, True)
                    
                    print("🎉 reCAPTCHA solved automatically! (No image challenge)")
                    return (True, False)
                    
            except Exception as e:
                print(f"⚠️ Failed with selector {selector}: {e}")
                continue
        
        return (False, False)
        
    except Exception as e:
        print(f"⚠️ reCAPTCHA click failed: {e}")
        return (False, False)

async def check_and_handle_captcha(page, client, last_b64_image):
    captcha_indicators = [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        ".g-recaptcha",
        "#captcha",
        "input[name='captcha']"
    ]
    
    captcha_found = False
    for selector in captcha_indicators:
        try:
            element = page.locator(selector).first
            if await element.count() > 0:
                print(f"🚨 CAPTCHA DETECTED (DOM): {selector}")
                captcha_found = True
                break
        except:
            continue
    
    if captcha_found:
        clicked, needs_images = await try_click_recaptcha(page)
        
        if clicked and not needs_images:
            print("🎉 reCAPTCHA bypassed! Continuing task...")
            await asyncio.sleep(2)
            return (False, None)
        elif clicked and needs_images:
            return (True, "reCAPTCHA image challenge appeared - switching to Bing")
    
    # Vision check fallback
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Look at this screenshot. Is there a CAPTCHA, 'I'm not a robot' checkbox, or verification challenge visible? Answer only YES or NO."},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{last_b64_image}"}}]}
            ],
            max_tokens=10
        )
        
        answer = response.choices[0].message.content.strip().upper()
        if "YES" in answer:
            print(f"🚨 CAPTCHA DETECTED (Vision): GPT-4o confirmed")
            clicked, needs_images = await try_click_recaptcha(page)
            if clicked and not needs_images:
                return (False, None)
            return (True, "CAPTCHA/Bot verification detected")
            
    except Exception as e:
        print(f"⚠️ Vision captcha check failed: {e}")
    
    return (False, None)

async def attempt_popup_bypass(page):
    try:
        close_selectors = [
            "button:has-text('Close')", "button:has-text('No thanks')",
            "button:has-text('Maybe later')", "button:has-text('Skip')",
            "[aria-label='Close']", ".close-button", ".modal-close",
            "button.close", "[class*='close']"
        ]
        
        for selector in close_selectors:
            try:
                element = page.locator(selector).first
                if await element.count() > 0:
                    await element.click(timeout=2000)
                    await asyncio.sleep(1)
                    return True
            except:
                continue
        
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
        return False
        
    except Exception as e:
        print(f"⚠️ Popup bypass failed: {e}")
        return False

async def detect_blocking_elements(page, b64_image, client):
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
                        "4. Cookie consent that blocks content\n\n"
                        "Return JSON:\n"
                        "{\"blocked\": true/false, \"blocker_type\": \"login\"|\"verification\"|\"cookies\"|\"none\", \"reason\": \"brief explanation\"}"
                    )
                },
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}]}
            ],
            max_tokens=150,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"blocked": False, "blocker_type": "none", "reason": "detection failed"}

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
            video_path, resource_type="video",
            public_id=f"agent_runs/{session_id}", overwrite=True
        )
        shutil.rmtree(folder_path)
        return upload_result.get("secure_url")
    except Exception as e:
        print(f"❌ Video Processing failed: {e}")
        if os.path.exists(folder_path):
             shutil.rmtree(folder_path)
        return None

async def analyze_failure(client, prompt, b64_image):
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a debugger. The agent failed to complete the task: '" + prompt + "'. Explain the BLOCKER in 1 sentence."},
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
    consecutive_failures = 0
    last_action = None
    action_repeat_count = 0
    blocker_detected = False

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-web-security",
                    "--window-size=1920,1080"
                ]
            ) 
            
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="America/New_York",
                permissions=["geolocation", "notifications"],
                geolocation={"latitude": 40.7128, "longitude": -74.0060},
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Upgrade-Insecure-Requests": "1"
                }
            )
            
            page = await context.new_page()
            await apply_ultimate_stealth(page)
            
            print(f"🚀 Starting Task: {request.prompt}")
            
            prompt_lower = request.prompt.lower()
            search_query = None
            
            if "search" in prompt_lower or "google" in prompt_lower or "find" in prompt_lower:
                search_query = prompt_lower
                for remove_word in ["go to", "google", "chrome", "search for", "search", "find", "look for"]:
                    search_query = search_query.replace(remove_word, "")
                search_query = search_query.strip()
            
            if search_query:
                await smart_bing_search(page, search_query)
            else:
                start_url = get_smart_start_url(request.prompt)
                await page.goto(start_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

            for step in range(1, 51):
                all_pages = context.pages
                if len(all_pages) > 0:
                    page = all_pages[-1]
                    await page.bring_to_front()

                last_b64_image = await get_b64_screenshot(page)
                
                img_path = f"{folder_name}/step_{step}.png"
                with open(img_path, "wb") as f:
                    f.write(base64.b64decode(last_b64_image))

                should_stop, captcha_reason = await check_and_handle_captcha(page, client, last_b64_image)
                if should_stop:
                    print(f"🛑 Captcha detected: {captcha_reason}")
                    if "google.com" in page.url and step < 10 and search_query:
                        print("🔄 Switching from Google to Bing to avoid captcha...")
                        await smart_bing_search(page, search_query)
                        consecutive_failures = 0
                        continue
                    
                    final_message = f"Failed: {captcha_reason}"
                    final_status = "failed"
                    blocker_detected = True
                    break

                if step % 3 == 0 or consecutive_failures > 2:
                    blocker_check = await detect_blocking_elements(page, last_b64_image, client)
                    if blocker_check.get("blocked"):
                        blocker_type = blocker_check.get("blocker_type")
                        reason = blocker_check.get("reason")
                        
                        if blocker_type == "login":
                            bypassed = await attempt_popup_bypass(page)
                            if not bypassed:
                                final_message = f"Failed: Login required - {reason}"
                                final_status = "failed"
                                blocker_detected = True
                                break
                            else:
                                await page.wait_for_timeout(2000)
                                consecutive_failures = 0
                                continue
                        
                        elif blocker_type == "cookies":
                            await attempt_popup_bypass(page)
                            await page.wait_for_timeout(1000)
                            continue

                system_prompt = (
                    "You are a human web user automating a task. Look at the screenshot carefully.\n\n"
                    f"FULL GOAL: {request.prompt}\n\n"
                    "CRITICAL RULES:\n"
                    "1. Break the goal into steps. Return 'done' ONLY when FULLY COMPLETED.\n"
                    "2. If you see 'Added to Cart', that's ONE step done. Continue if needed.\n"
                    "3. If you see a CAPTCHA image puzzle, return action='done' with reason='Blocked by captcha'.\n"
                    "4. If you see an unclosable login popup, return action='done' with reason='Blocked by login'.\n"
                    f"Current Step: {step}/50\n"
                    "\nReturn JSON ONLY:\n"
                    "{\"action\": \"click\"|\"type\"|\"done\", \"label\": \"visible_text_or_aria_label\", \"text_to_type\": \"...\", \"reason\": \"...\"}"
                )

                response = await client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{last_b64_image}"}}]}
                    ],
                    max_tokens=300,
                    response_format={"type": "json_object"}
                )

                decision = json.loads(response.choices[0].message.content)
                current_action = decision.get('action', 'unknown')
                reason = decision.get('reason', 'No reason provided')
                
                print(f"📍 Step {step}: {current_action} -> {reason}")
                
                if current_action == last_action:
                    action_repeat_count += 1
                else:
                    action_repeat_count = 0
                    last_action = current_action
                
                if action_repeat_count >= 5:
                    final_message = f"Failed: Stuck in loop with action '{current_action}'"
                    break

                if current_action == 'done':
                    reason_lower = reason.lower()
                    if any(word in reason_lower for word in ["captcha", "verification", "blocked", "login"]):
                        final_message = f"Failed: {reason}"
                        final_status = "failed"
                        blocker_detected = True
                        break
                    
                    final_message = f"Success: {reason}"
                    final_status = "success"
                    break
                
                action_succeeded = False
                
                try:
                    if current_action == 'click':
                        label = decision.get('label', '')
                        element = page.get_by_role("link", name=label).first
                        if await element.count() == 0:
                            element = page.get_by_role("button", name=label).first
                        if await element.count() == 0:
                            element = page.locator("input, textarea, button").filter(has_text=label).first
                        if await element.count() == 0:
                            element = page.get_by_text(label, exact=False).first

                        if await element.count() > 0:
                            # Use our new human-like mouse movement to click normal elements too
                            box = await element.bounding_box()
                            if box:
                                target_x = box['x'] + (box['width'] / 2)
                                target_y = box['y'] + (box['height'] / 2)
                                await human_like_mouse_move(page, target_x, target_y)
                                await page.mouse.click(target_x, target_y)
                                await asyncio.sleep(2)
                                action_succeeded = True
                        else:
                            print(f"⚠️ Could not find element: {label}")

                    elif current_action == 'type':
                        search_box = page.locator("input[type='text'], input[type='search'], [aria-label='Search']").first
                        await search_box.click(timeout=5000)
                        await search_box.type(decision.get('text_to_type', ''), delay=random.randint(50, 150))
                        await page.keyboard.press("Enter")
                        await page.wait_for_timeout(4000)
                        action_succeeded = True
                
                except Exception as ex:
                    print(f"⚠️ Action error: {ex}")
                    action_succeeded = False
                
                if action_succeeded:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                
                if consecutive_failures >= 8:
                    final_message = "Failed: Too many consecutive failures"
                    break
            
            if final_status == "failed" and last_b64_image and not blocker_detected:
                error_reason = await analyze_failure(client, request.prompt, last_b64_image)
                final_message = f"Failed: {error_reason}"

            await browser.close()
            
            print("🏁 Generating video proof...")
            video_url = await create_and_upload_video(folder_name, session_id)

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
