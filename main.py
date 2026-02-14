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
# 3. STEALTH & HUMAN MOVEMENT HELPERS
# -------------------------------------------------

async def get_b64_screenshot(page):
    try:
        # Wait for the network to actually quiet down
        await page.wait_for_load_state("networkidle", timeout=4000)
    except:
        pass
    
    await asyncio.sleep(1.5) # Extra buffer for animations like Turnstile
    screenshot_bytes = await page.screenshot()
    return base64.b64encode(screenshot_bytes).decode("utf-8")

async def apply_ultimate_stealth(page):
    """Maximum stealth - injects realistic browser fingerprints"""
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
        Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });
        window.chrome = { runtime: {}, app: {} };
    """)

async def human_like_mouse_move(page, dest_x, dest_y):
    """Simulates organic mouse movement with jitter and ease-in/out."""
    start_x = random.randint(100, 800)
    start_y = random.randint(100, 600)
    
    await page.mouse.move(start_x, start_y)
    await asyncio.sleep(random.uniform(0.1, 0.3))

    steps = random.randint(15, 30)
    for i in range(steps):
        t = i / steps
        ease_t = t * t * (3 - 2 * t)
        
        jitter_x = random.uniform(-3, 3)
        jitter_y = random.uniform(-3, 3)
        
        curr_x = start_x + (dest_x - start_x) * ease_t + jitter_x
        curr_y = start_y + (dest_y - start_y) * ease_t + jitter_y
        
        await page.mouse.move(curr_x, curr_y)
        await asyncio.sleep(random.uniform(0.01, 0.04))
        
    await page.mouse.move(dest_x, dest_y)

# -------------------------------------------------
# 4. CAPTCHA SOLVING LOGIC (THE FREE WAY)
# -------------------------------------------------

async def handle_recaptcha_grid(page, client):
    """Uses GPT-4o to analyze the 3x3 grid and calculates coordinates to click."""
    try:
        bframe = None
        for f in page.frames:
            if 'bframe' in f.url:
                bframe = f
                break
                
        if not bframe:
            return False

        await asyncio.sleep(2) # Let images load
        
        # 1. Get the instruction (e.g., "Select all squares with crosswalks")
        instruction_el = bframe.locator(".rc-imageselect-instructions").first
        if await instruction_el.count() == 0:
            return False
            
        instruction_text = await instruction_el.inner_text()
        instruction_text = instruction_text.replace('\n', ' ')

        # 2. Get the grid bounding box
        grid_el = bframe.locator(".rc-imageselect-target").first
        if await grid_el.count() == 0:
            return False
            
        box = await grid_el.bounding_box()
        if not box:
            return False

        # 3. Screenshot just the iframe puzzle to save tokens
        bframe_element = page.locator("iframe[src*='bframe']").first
        bframe_bytes = await bframe_element.screenshot()
        b64_img = base64.b64encode(bframe_bytes).decode("utf-8")

        print(f"🧠 Asking GPT-4o to solve grid for: {instruction_text}")

        # 4. Ask GPT-4o
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": f"You are a CAPTCHA solver. Look at this 3x3 grid. The instruction is: '{instruction_text}'. Return ONLY a JSON array of integers (1-9, reading left-to-right, top-to-bottom) for the squares that match the instruction. If none match, return an empty array. Example format: {{\"squares\": [1, 5, 9]}}"
                },
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}}]}
            ],
            response_format={"type": "json_object"},
            max_tokens=50
        )

        data = json.loads(response.choices[0].message.content)
        squares = data.get("squares", [])
        print(f"🎯 GPT-4o selected squares: {squares}")

        # 5. Calculate coordinates and click organically
        cell_w = box['width'] / 3
        cell_h = box['height'] / 3

        for sq in squares:
            if not (1 <= sq <= 9): continue
            row = (sq - 1) // 3
            col = (sq - 1) % 3
            
            # Center of the specific cell
            target_x = box['x'] + (col * cell_w) + (cell_w / 2) + random.uniform(-5, 5)
            target_y = box['y'] + (row * cell_h) + (cell_h / 2) + random.uniform(-5, 5)

            await human_like_mouse_move(page, target_x, target_y)
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.08, 0.2))
            await page.mouse.up()
            await asyncio.sleep(random.uniform(0.4, 1.0))

        # 6. Click Verify
        verify_btn = bframe.locator("#recaptcha-verify-button").first
        if await verify_btn.count() > 0:
            v_box = await verify_btn.bounding_box()
            if v_box:
                vx = v_box['x'] + (v_box['width'] / 2)
                vy = v_box['y'] + (v_box['height'] / 2)
                await human_like_mouse_move(page, vx, vy)
                await page.mouse.click(vx, vy)

        await asyncio.sleep(4) # Wait for fade out or next challenge
        return True

    except Exception as e:
        print(f"⚠️ Grid solver failed: {e}")
        return False

async def try_click_recaptcha(page):
    """Attempt to click the initial checkbox like a human"""
    try:
        recaptcha_frame = None
        for frame in page.frames:
            if 'recaptcha' in frame.url and 'anchor' in frame.url:
                recaptcha_frame = frame
                break
        
        if not recaptcha_frame:
            return (False, False)
        
        checkbox_selectors = [".recaptcha-checkbox-border", "#recaptcha-anchor"]
        
        for selector in checkbox_selectors:
            checkbox = recaptcha_frame.locator(selector).first
            if await checkbox.count() > 0:
                print(f"✅ Found checkbox! Simulating human movement...")
                await checkbox.scroll_into_view_if_needed()
                await asyncio.sleep(0.5)
                
                box = await checkbox.bounding_box()
                if not box: continue
                
                target_x = box['x'] + (box['width'] / 2) + random.uniform(-4, 4)
                target_y = box['y'] + (box['height'] / 2) + random.uniform(-4, 4)
                
                await human_like_mouse_move(page, target_x, target_y)
                await asyncio.sleep(random.uniform(0.3, 0.7))
                await page.mouse.down()
                await asyncio.sleep(random.uniform(0.08, 0.2))
                await page.mouse.up()
                
                await asyncio.sleep(random.uniform(3.0, 4.5))
                
                # Check if image grid appeared
                for f in page.frames:
                    if 'recaptcha' in f.url and 'bframe' in f.url:
                        return (True, True) # Clicked, but needs image solver
                
                return (True, False) # Solved instantly
                
        return (False, False)
    except:
        return (False, False)

async def check_and_handle_captcha(page, client, last_b64_image):
    
    # 1. Handle Cloudflare Turnstile (Spinning circle)
    turnstile = page.locator(".cf-turnstile, [id^='cf-']").first
    if await turnstile.count() > 0:
        print("🚨 Cloudflare Turnstile detected. Waiting for auto-verify...")
        await asyncio.sleep(6) # Real Chrome usually passes this automatically
        return (False, None)
        
    # 2. Handle Google reCAPTCHA v2 Checkbox
    recaptcha = page.locator("iframe[src*='recaptcha']").first
    if await recaptcha.count() > 0:
        clicked, needs_images = await try_click_recaptcha(page)
        
        if clicked and not needs_images:
            print("🎉 reCAPTCHA bypassed instantly!")
            return (False, None)
            
        elif clicked and needs_images:
            print("🧩 Image grid appeared! Firing up GPT-4o vision solver...")
            # We try solving it up to 2 times (sometimes it gives multiple pages)
            for _ in range(2):
                await handle_recaptcha_grid(page, client)
                await asyncio.sleep(2)
                
                # Check if still there
                grid_still_there = False
                for f in page.frames:
                    if 'bframe' in f.url and await f.locator("#recaptcha-verify-button").count() > 0:
                        grid_still_there = True
                        break
                        
                if not grid_still_there:
                    print("🎉 GPT-4o successfully solved the grid!")
                    return (False, None)
                    
            return (True, "Failed to solve image grid after multiple attempts.")
            
    return (False, None)

# -------------------------------------------------
# 5. GENERAL BROWSER HELPERS
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
# 6. API ENDPOINT (THE MAIN LOOP)
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
            # 🔥 CRITICAL FIX: channel="chrome" uses REAL Google Chrome to bypass Cloudflare
            browser = await p.chromium.launch(
                channel="chrome", 
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
