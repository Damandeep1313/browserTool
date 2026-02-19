import os
import base64
import json
import asyncio
import uuid
import subprocess
import shutil
import urllib.parse
import httpx
import re as _re
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

if not CAPSOLVER_API_KEY:
    raise RuntimeError("CAPSOLVER_API_KEY not found in .env")

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
    extracted_content: dict | None = None

# -------------------------------------------------
# 4. CAPSOLVER INTEGRATION
# -------------------------------------------------

async def solve_cloudflare_turnstile(page_url: str, site_key: str) -> dict:
    try:
        print(f"🔧 CapSolver: Solving Cloudflare Turnstile...")
        async with httpx.AsyncClient() as client:
            create_response = await client.post(
                "https://api.capsolver.com/createTask",
                json={
                    "clientKey": CAPSOLVER_API_KEY,
                    "task": {
                        "type": "AntiTurnstileTaskProxyLess",
                        "websiteURL": page_url,
                        "websiteKey": site_key
                    }
                },
                timeout=30.0
            )
            create_data = create_response.json()
            if create_data.get("errorId") != 0:
                return {"success": False, "error": create_data.get('errorDescription')}
            task_id = create_data.get("taskId")
            print(f"✅ CapSolver task created: {task_id}")
            for attempt in range(60):
                await asyncio.sleep(2)
                result_response = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id},
                    timeout=30.0
                )
                result_data = result_response.json()
                if result_data.get("status") == "ready":
                    print(f"✅ CapSolver: Cloudflare Turnstile solved!")
                    return {"success": True, "solution": result_data.get("solution", {}).get("token")}
                elif result_data.get("status") == "failed":
                    return {"success": False, "error": result_data.get('errorDescription')}
                if attempt % 10 == 0:
                    print(f"⏳ CapSolver: Still solving... ({attempt * 2}s)")
            return {"success": False, "error": "Timeout waiting for solution"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def solve_recaptcha_v2(page_url: str, site_key: str) -> dict:
    try:
        print(f"🔧 CapSolver: Solving reCAPTCHA v2...")
        async with httpx.AsyncClient() as client:
            create_response = await client.post(
                "https://api.capsolver.com/createTask",
                json={
                    "clientKey": CAPSOLVER_API_KEY,
                    "task": {
                        "type": "ReCaptchaV2TaskProxyLess",
                        "websiteURL": page_url,
                        "websiteKey": site_key
                    }
                },
                timeout=30.0
            )
            create_data = create_response.json()
            if create_data.get("errorId") != 0:
                return {"success": False, "error": create_data.get('errorDescription')}
            task_id = create_data.get("taskId")
            print(f"✅ CapSolver task created: {task_id}")
            for attempt in range(60):
                await asyncio.sleep(2)
                result_response = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id},
                    timeout=30.0
                )
                result_data = result_response.json()
                if result_data.get("status") == "ready":
                    print(f"✅ CapSolver: reCAPTCHA v2 solved!")
                    return {"success": True, "solution": result_data.get("solution", {}).get("gRecaptchaResponse")}
                elif result_data.get("status") == "failed":
                    return {"success": False, "error": result_data.get('errorDescription')}
                if attempt % 10 == 0:
                    print(f"⏳ CapSolver: Still solving... ({attempt * 2}s)")
            return {"success": False, "error": "Timeout waiting for solution"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def solve_hcaptcha(page_url: str, site_key: str) -> dict:
    try:
        print(f"🔧 CapSolver: Solving hCaptcha...")
        async with httpx.AsyncClient() as client:
            create_response = await client.post(
                "https://api.capsolver.com/createTask",
                json={
                    "clientKey": CAPSOLVER_API_KEY,
                    "task": {
                        "type": "HCaptchaTaskProxyLess",
                        "websiteURL": page_url,
                        "websiteKey": site_key
                    }
                },
                timeout=30.0
            )
            create_data = create_response.json()
            if create_data.get("errorId") != 0:
                return {"success": False, "error": create_data.get('errorDescription')}
            task_id = create_data.get("taskId")
            for attempt in range(60):
                await asyncio.sleep(2)
                result_response = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id},
                    timeout=30.0
                )
                result_data = result_response.json()
                if result_data.get("status") == "ready":
                    print(f"✅ CapSolver: hCaptcha solved!")
                    return {"success": True, "solution": result_data.get("solution", {}).get("gRecaptchaResponse")}
                elif result_data.get("status") == "failed":
                    return {"success": False, "error": result_data.get('errorDescription')}
            return {"success": False, "error": "Timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def detect_and_solve_captcha(page) -> tuple[bool, str | None]:
    try:
        page_url = page.url
        turnstile_sitekey = None

        try:
            frames = page.frames
            for frame in frames:
                if 'challenges.cloudflare.com' in frame.url or 'turnstile' in frame.url.lower():
                    print("✅ Detected Cloudflare Turnstile iframe")
                    try:
                        content = await page.content()
                        match = _re.search(r'data-sitekey=["\']([^"\']+)["\']', content)
                        if match:
                            turnstile_sitekey = match.group(1)
                            break
                    except:
                        pass
                    break
        except:
            pass

        if not turnstile_sitekey:
            try:
                for selector in ["[data-sitekey]", ".cf-turnstile", "#cf-turnstile",
                                  "iframe[src*='turnstile']", "iframe[src*='challenges.cloudflare']"]:
                    element = page.locator(selector).first
                    if await element.count() > 0:
                        try:
                            turnstile_sitekey = await element.get_attribute("data-sitekey")
                            if turnstile_sitekey:
                                break
                        except:
                            content = await page.content()
                            match = _re.search(r'data-sitekey=["\']([^"\']+)["\']', content)
                            if match:
                                turnstile_sitekey = match.group(1)
                                break
            except:
                pass

        if turnstile_sitekey:
            print(f"🎯 Detected Cloudflare Turnstile - Solving with CapSolver...")
            result = await solve_cloudflare_turnstile(page_url, turnstile_sitekey)
            if result.get("success"):
                solution_token = result.get("solution")
                try:
                    await page.evaluate(f"""
                        (token) => {{
                            const inputs = document.querySelectorAll('input[name*="cf-turnstile-response"], input[name*="turnstile"]');
                            inputs.forEach(input => {{
                                input.value = token;
                                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }});
                            const turnstileElement = document.querySelector('.cf-turnstile, [data-sitekey]');
                            if (turnstileElement) {{
                                const callback = turnstileElement.getAttribute('data-callback');
                                if (callback && typeof window[callback] === 'function') {{
                                    try {{ window[callback](token); }} catch(e) {{}}
                                }}
                            }}
                        }}
                    """, solution_token)
                    print("✅ Turnstile solution injected!")
                except Exception as e:
                    print(f"⚠️ Turnstile injection failed: {e}")
                await asyncio.sleep(3)
                still_has_turnstile = await page.evaluate("""
                    () => {
                        const frames = document.querySelectorAll('iframe');
                        for (let frame of frames) {
                            if (frame.src.includes('challenges.cloudflare') || frame.src.includes('turnstile')) return true;
                        }
                        return false;
                    }
                """)
                if still_has_turnstile:
                    print("⚠️ Turnstile still present")
                    return (False, None)
                else:
                    print("✅ Turnstile passed!")
                    return (True, None)
            else:
                return (False, None)

        print("🤖 Checking for reCAPTCHA...")
        checkbox_clicked = await try_click_recaptcha_checkbox(page)
        if checkbox_clicked:
            await asyncio.sleep(4)
            has_image_challenge = await page.evaluate("""
                () => {
                    const frames = document.querySelectorAll('iframe');
                    for (let frame of frames) {
                        if (frame.src.includes('recaptcha') && frame.src.includes('bframe')) return true;
                    }
                    return false;
                }
            """)
            if not has_image_challenge:
                print("✅ reCAPTCHA checkbox click was enough!")
                return (True, None)

        recaptcha_sitekey = None
        try:
            frames = page.frames
            for frame in frames:
                if 'recaptcha' in frame.url and 'anchor' in frame.url:
                    match = _re.search(r'[?&]k=([^&]+)', frame.url)
                    if match:
                        recaptcha_sitekey = match.group(1)
                        break
        except:
            pass

        if not recaptcha_sitekey:
            try:
                sitekey_element = page.locator("[data-sitekey]").first
                if await sitekey_element.count() > 0:
                    recaptcha_sitekey = await sitekey_element.get_attribute("data-sitekey")
            except:
                pass

        if not recaptcha_sitekey:
            try:
                content = await page.content()
                match = _re.search(r'data-sitekey=["\']([^"\']+)["\']', content)
                if match:
                    recaptcha_sitekey = match.group(1)
            except:
                pass

        if recaptcha_sitekey:
            print(f"🎯 Detected reCAPTCHA v2 - Solving with CapSolver...")
            result = await solve_recaptcha_v2(page_url, recaptcha_sitekey)
            if result.get("success"):
                solution_token = result.get("solution")
                try:
                    await page.evaluate(f"""
                        (token) => {{
                            document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {{
                                el.innerHTML = token; el.value = token; el.style.display = 'block';
                            }});
                            const textarea = document.getElementById('g-recaptcha-response');
                            if (textarea) {{ textarea.innerHTML = token; textarea.value = token; }}
                            const elements = document.querySelectorAll('[data-callback]');
                            elements.forEach(el => {{
                                const callback = el.getAttribute('data-callback');
                                if (callback && typeof window[callback] === 'function') {{
                                    try {{ window[callback](token); }} catch(e) {{}}
                                }}
                            }});
                        }}
                    """, solution_token)
                    print("✅ reCAPTCHA solution injected!")
                except Exception as e:
                    print(f"⚠️ Injection failed: {e}")

                await asyncio.sleep(3)
                submitted = False
                try:
                    form_submitted = await page.evaluate("""
                        () => {
                            const textarea = document.querySelector('textarea[name="g-recaptcha-response"]');
                            if (textarea) { const form = textarea.closest('form'); if (form) { form.submit(); return true; } }
                            return false;
                        }
                    """)
                    if form_submitted:
                        submitted = True
                except:
                    pass

                if not submitted:
                    for selector in ["button[type='submit']", "input[type='submit']",
                                     "button:has-text('Submit')", "button:has-text('Continue')",
                                     "button:has-text('Verify')", "[type='submit']", "form button"]:
                        try:
                            element = page.locator(selector).first
                            if await element.count() > 0:
                                await element.click(timeout=3000)
                                submitted = True
                                break
                        except:
                            continue

                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    await asyncio.sleep(3)
                except:
                    await asyncio.sleep(5)

                still_has_captcha = await page.evaluate("""
                    () => {
                        const frames = document.querySelectorAll('iframe');
                        for (let frame of frames) {
                            if (frame.src.includes('recaptcha') && frame.src.includes('anchor')) return true;
                        }
                        return false;
                    }
                """)
                if still_has_captcha:
                    return (False, None)
                else:
                    print("✅ CAPTCHA passed!")
                    return (True, None)
            else:
                return (False, f"CapSolver failed: {result.get('error')}")

        hcaptcha_sitekey = None
        try:
            hcaptcha_element = page.locator("[data-sitekey]").first
            if await hcaptcha_element.count() > 0:
                parent_html = await hcaptcha_element.evaluate("el => el.outerHTML")
                if "hcaptcha" in parent_html.lower():
                    hcaptcha_sitekey = await hcaptcha_element.get_attribute("data-sitekey")
        except:
            pass

        if hcaptcha_sitekey:
            result = await solve_hcaptcha(page_url, hcaptcha_sitekey)
            if result.get("success"):
                solution_token = result.get("solution")
                await page.evaluate(f"""
                    (token) => {{
                        const textarea = document.querySelector('[name="h-captcha-response"]');
                        if (textarea) {{ textarea.innerHTML = token; textarea.value = token; }}
                    }}
                """, solution_token)
                print("✅ hCaptcha solution injected!")
                await asyncio.sleep(2)
                return (True, None)
            else:
                return (False, f"CapSolver failed: {result.get('error')}")

        return (False, None)

    except Exception as e:
        print(f"❌ CAPTCHA detection/solving error: {e}")
        return (False, f"Error: {str(e)}")


# -------------------------------------------------
# 5. HELPERS
# -------------------------------------------------

async def try_click_recaptcha_checkbox(page) -> bool:
    try:
        recaptcha_frame = None
        for frame in page.frames:
            if 'recaptcha' in frame.url and 'anchor' in frame.url:
                recaptcha_frame = frame
                print(f"✅ Found reCAPTCHA anchor iframe")
                break

        if not recaptcha_frame:
            print("⚠️ No reCAPTCHA anchor iframe found")
            return False

        for selector in [".recaptcha-checkbox-border", "#recaptcha-anchor",
                         ".rc-anchor-center-item", "div.recaptcha-checkbox-checkmark", ".recaptcha-checkbox"]:
            try:
                checkbox = recaptcha_frame.locator(selector).first
                if await checkbox.count() > 0:
                    await asyncio.sleep(0.5)
                    await checkbox.hover()
                    await asyncio.sleep(0.3)
                    await checkbox.click(timeout=3000)
                    print("✅ Clicked reCAPTCHA checkbox!")
                    return True
            except Exception as e:
                continue

        print("⚠️ Could not find clickable checkbox")
        return False
    except Exception as e:
        print(f"⚠️ Checkbox click failed: {e}")
        return False


async def extract_page_content(page):
    try:
        content = {
            "url": page.url,
            "title": await page.title(),
            "text": "",
            "images": []
        }
        try:
            text_content = await page.evaluate("""
                () => {
                    const elements = document.querySelectorAll('script, style, [hidden]');
                    elements.forEach(el => el.remove());
                    for (let selector of ['main', 'article', '[role="main"]', '.content', '#content', 'body']) {
                        const element = document.querySelector(selector);
                        if (element) return element.innerText.trim();
                    }
                    return document.body.innerText.trim();
                }
            """)
            if text_content:
                lines = [line.strip() for line in text_content.split('\n') if line.strip()]
                content["text"] = '\n'.join(lines[:100])
        except Exception as e:
            print(f"⚠️ Text extraction error: {e}")
        try:
            images = await page.evaluate("""
                () => Array.from(document.querySelectorAll('img'))
                    .filter(img => img.src && img.width > 100 && img.height > 100)
                    .slice(0, 10)
                    .map(img => ({ src: img.src, alt: img.alt || '', width: img.width, height: img.height }))
            """)
            content["images"] = images
        except Exception as e:
            print(f"⚠️ Image extraction error: {e}")
        print(f"📄 Extracted content: {len(content['text'])} chars, {len(content['images'])} images")
        return content
    except Exception as e:
        print(f"❌ Content extraction failed: {e}")
        return None


async def get_b64_screenshot(page):
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=3000)
    except:
        pass
    screenshot_bytes = await page.screenshot()
    return base64.b64encode(screenshot_bytes).decode("utf-8")


async def apply_ultimate_stealth(page):
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                { 0: {type: "application/pdf"}, description: "Portable Document Format", filename: "internal-pdf-viewer", length: 1, name: "Chrome PDF Plugin" },
                { 0: {type: "application/x-google-chrome-pdf"}, description: "Portable Document Format", filename: "internal-pdf-viewer", length: 1, name: "Chrome PDF Viewer" }
            ]
        });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
        Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.' });
        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    """)


def get_smart_start_url(prompt: str):
    prompt_lower = prompt.lower()
    if "amazon" in prompt_lower: return "https://www.amazon.in"
    elif "flipkart" in prompt_lower: return "https://www.flipkart.com"
    elif "youtube" in prompt_lower: return "https://www.youtube.com"
    elif "myntra" in prompt_lower: return "https://www.myntra.com"
    elif "swiggy" in prompt_lower: return "https://www.swiggy.com"
    elif "zomato" in prompt_lower: return "https://www.zomato.com"
    return "https://search.brave.com"


async def smart_brave_search(page, query: str):
    try:
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://search.brave.com/search?q={encoded_query}"
        print(f"🎯 Brave search URL: {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        return True
    except Exception as e:
        print(f"⚠️ Brave search failed: {e}")
        return False


async def check_and_handle_captcha(page, client, last_b64_image):
    solved, error = await detect_and_solve_captcha(page)
    if solved:
        print("🎉 CAPTCHA solved by CapSolver! Continuing task...")
        await asyncio.sleep(2)
        return (False, None)

    for selector in ["iframe[src*='recaptcha']", "iframe[src*='hcaptcha']",
                     "iframe[src*='turnstile']", "iframe[src*='challenges.cloudflare']",
                     ".g-recaptcha", "div[class*='rc-anchor']", "div.h-captcha", ".cf-turnstile"]:
        try:
            element = page.locator(selector).first
            if await element.count() > 0 and await element.is_visible():
                print(f"🚨 CAPTCHA still visible: {selector}")
                solved, error = await detect_and_solve_captcha(page)
                if solved:
                    return (False, None)
                print("⚠️ Continuing despite CAPTCHA...")
                return (False, None)
        except:
            continue

    return (False, None)


async def attempt_popup_bypass(page):
    try:
        for selector in ["button:has-text('Close')", "button:has-text('No thanks')",
                         "button:has-text('Maybe later')", "button:has-text('Skip')",
                         "button:has-text('Not now')", "button:has-text('Accept')",
                         "button:has-text('Got it')", "[aria-label='Close']",
                         ".close-button", ".modal-close", "[data-dismiss='modal']",
                         "button.close", "[class*='close']"]:
            try:
                element = page.locator(selector).first
                if await element.count() > 0:
                    await element.click(timeout=2000)
                    await asyncio.sleep(1)
                    print(f"✅ Closed popup using: {selector}")
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
            model="gpt-4o-mini",
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
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}]}
            ],
            max_tokens=150,
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        if not content or content.strip() == "":
            return {"blocked": False, "blocker_type": "none", "reason": "detection failed"}
        return json.loads(content)
    except json.JSONDecodeError:
        return {"blocked": False, "blocker_type": "none", "reason": "JSON parsing failed"}
    except Exception as e:
        return {"blocked": False, "blocker_type": "none", "reason": "detection failed"}


# -------------------------------------------------
# VIDEO CREATION
# -------------------------------------------------
async def create_and_upload_video(folder_path: str, session_id: str) -> str | None:
    try:
        import glob
        screenshots = glob.glob(f"{folder_path}/step_*.png")

        def extract_step_number(filename):
            match = _re.search(r'step_(\d+)', filename)
            return int(match.group(1)) if match else 999999

        screenshots = sorted(screenshots, key=extract_step_number)
        if len(screenshots) == 0:
            print("⚠️ No screenshots found for video")
            return None

        print(f"📹 Creating video from {len(screenshots)} screenshots...")
        abs_folder = os.path.abspath(folder_path)
        abs_screenshots = [os.path.abspath(s) for s in screenshots]
        abs_video_path = os.path.join(abs_folder, "output.mp4")
        abs_file_list = os.path.join(abs_folder, "file_list.txt")

        with open(abs_file_list, 'w') as f:
            for screenshot in abs_screenshots:
                f.write(f"file '{screenshot}'\n")
                f.write(f"duration 2\n")
            if abs_screenshots:
                f.write(f"file '{abs_screenshots[-1]}'\n")

        command = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", abs_file_list,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:-1:-1:color=black",
            abs_video_path
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        print(f"✅ Video created: {abs_video_path}")

        upload_result = cloudinary.uploader.upload(
            abs_video_path,
            resource_type="video",
            public_id=f"agent_runs/{session_id}",
            overwrite=True,
            chunk_size=6000000
        )
        video_url = upload_result.get("secure_url")
        print(f"✅ Video uploaded: {video_url}")
        shutil.rmtree(folder_path)
        return video_url
    except subprocess.CalledProcessError as e:
        print(f"❌ FFmpeg error: {e.stderr}")
        if os.path.exists(folder_path):
            shutil.rmtree(folder_path)
        return None
    except Exception as e:
        print(f"❌ Video processing failed: {e}")
        if os.path.exists(folder_path):
            shutil.rmtree(folder_path)
        return None


async def analyze_failure(client, prompt, b64_image):
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"You are a debugger. The agent failed to complete the task: '{prompt}'. Look at the screenshot. Is there a Login Popup? Captcha? Item out of stock? Explain the BLOCKER in 1 sentence."
                },
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}]}
            ],
            max_tokens=100
        )
        content = response.choices[0].message.content
        return content if content else "Unknown error."
    except:
        return "Unknown error (could not analyze)."


# -------------------------------------------------
# CORE FIX: Click the '+' button INSIDE a specific tool card
# Strategy: find the tool by name → find button inside its card → click it
# Works generically on any site (no hardcoded classes/coords)
# -------------------------------------------------

async def click_add_button_for_tool(page, tool_name: str, openai_client=None, screenshot_b64: str = None) -> bool:
    """
    Find the add/plus button for a specific tool card on any marketplace/list page.

    The key insight: instead of searching for a '+' button globally (which finds
    the wrong one), we find the TOOL CARD containing the tool name, then find
    the clickable button INSIDE that card.

    This is robust against any CSS class naming convention.
    """
    print(f"🎯 Looking for add button for tool: '{tool_name}'")

    tool_words = [w for w in tool_name.lower().split() if len(w) > 2]

    # ── Strategy 1: Find tool card by text → find button inside it ──
    # Walk up from text node to find the card container, then find buttons in it
    try:
        result = await page.evaluate(f"""
            () => {{
                const toolWords = {json.dumps(tool_words)};
                const toolName = {json.dumps(tool_name.lower())};

                // Find all text nodes that match the tool name
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let bestCard = null;
                let bestScore = 0;

                let node;
                while (node = walker.nextNode()) {{
                    const text = (node.textContent || '').toLowerCase().trim();

                    // Score this text node by how well it matches the tool name
                    let score = 0;
                    if (text === toolName) score = 100;
                    else if (text.includes(toolName)) score = 80;
                    else {{
                        const matchedWords = toolWords.filter(w => text.includes(w));
                        score = (matchedWords.length / toolWords.length) * 60;
                    }}

                    if (score < 30) continue;

                    const textEl = node.parentElement;
                    if (!textEl) continue;
                    const textRect = textEl.getBoundingClientRect();
                    if (textRect.width === 0 || textRect.y < 60) continue;

                    // Walk UP the DOM to find the card container
                    // A card is a container that:
                    // 1. Contains the text element
                    // 2. Also contains at least one button/clickable
                    // 3. Is reasonably sized (not the whole page)
                    let container = textEl;
                    for (let depth = 0; depth < 8; depth++) {{
                        container = container.parentElement;
                        if (!container || container === document.body) break;

                        const rect = container.getBoundingClientRect();
                        // Skip if too large (likely the whole list/page)
                        if (rect.width > window.innerWidth * 0.9) continue;
                        // Skip if too small
                        if (rect.width < 50 || rect.height < 20) continue;

                        // Look for buttons/clickables inside this container
                        const buttons = Array.from(container.querySelectorAll(
                            'button, [role="button"], a[href]'
                        )).filter(btn => {{
                            const r = btn.getBoundingClientRect();
                            return r.width > 0 && r.height > 0 && r.width < 200;
                        }});

                        if (buttons.length === 0) continue;

                        // Found a card with buttons — pick the best button
                        // Prefer: buttons with SVG (icon buttons), small buttons, rightmost button
                        let bestBtn = null;
                        let bestBtnScore = -1;

                        for (const btn of buttons) {{
                            const r = btn.getBoundingClientRect();
                            let btnScore = 0;

                            // Prefer small square buttons (icon buttons like +)
                            const isSmall = r.width <= 60 && r.height <= 60;
                            if (isSmall) btnScore += 30;

                            // Prefer buttons with SVG (icon-only buttons)
                            if (btn.querySelector('svg')) btnScore += 20;

                            // Prefer buttons on the right side of the card
                            btnScore += (r.x / window.innerWidth) * 20;

                            // Avoid buttons that are likely "view details" or nav links
                            const btnText = (btn.textContent || '').toLowerCase().trim();
                            if (btnText.length > 20) btnScore -= 20;
                            if (btnText.includes('view') || btnText.includes('detail')) btnScore -= 30;

                            if (btnScore > bestBtnScore) {{
                                bestBtnScore = btnScore;
                                bestBtn = {{
                                    x: r.x + r.width / 2,
                                    y: r.y + r.height / 2,
                                    w: r.width,
                                    h: r.height,
                                    text: btnText.substring(0, 20),
                                    hasSvg: !!btn.querySelector('svg')
                                }};
                            }}
                        }}

                        if (bestBtn && score + bestBtnScore > bestScore) {{
                            bestScore = score + bestBtnScore;
                            bestCard = {{ ...bestBtn, cardDepth: depth, textScore: score }};
                        }}

                        // Once we found a reasonably-sized card, stop walking up
                        if (rect.height < window.innerHeight * 0.3 && buttons.length > 0) break;
                    }}
                }}

                return bestCard;
            }}
        """)

        if result and result.get('x'):
            print(f"✅ Strategy 1: Found tool card button at ({result['x']:.0f}, {result['y']:.0f}) "
                  f"size={result.get('w',0):.0f}x{result.get('h',0):.0f} "
                  f"hasSvg={result.get('hasSvg')} text='{result.get('text','')}'")
            await page.mouse.click(result['x'], result['y'])
            await asyncio.sleep(2)
            return True
    except Exception as e:
        print(f"⚠️ Strategy 1 failed: {e}")

    # ── Strategy 2: Scroll and search — tool might be below the fold ──
    # Scroll down incrementally and re-run strategy 1
    try:
        viewport_height = (page.viewport_size or {"height": 1080})["height"]
        page_height = await page.evaluate("() => document.body.scrollHeight")

        for scroll_pos in range(0, min(page_height, 3000), viewport_height // 2):
            await page.evaluate(f"window.scrollTo(0, {scroll_pos})")
            await asyncio.sleep(0.5)

            result = await page.evaluate(f"""
                () => {{
                    const toolWords = {json.dumps(tool_words)};
                    const toolName = {json.dumps(tool_name.lower())};

                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    let node;

                    while (node = walker.nextNode()) {{
                        const text = (node.textContent || '').toLowerCase().trim();
                        const matchedWords = toolWords.filter(w => text.includes(w));
                        if (matchedWords.length < Math.min(2, toolWords.length)) continue;

                        const textEl = node.parentElement;
                        if (!textEl) continue;
                        const textRect = textEl.getBoundingClientRect();

                        // Only elements visible in current viewport
                        if (textRect.y < 0 || textRect.y > window.innerHeight) continue;
                        if (textRect.width === 0) continue;

                        // Walk up to find card with buttons
                        let container = textEl;
                        for (let d = 0; d < 8; d++) {{
                            container = container.parentElement;
                            if (!container || container === document.body) break;

                            const rect = container.getBoundingClientRect();
                            if (rect.width > window.innerWidth * 0.9) continue;

                            const buttons = Array.from(container.querySelectorAll(
                                'button, [role="button"]'
                            )).filter(btn => {{
                                const r = btn.getBoundingClientRect();
                                return r.width > 0 && r.width <= 80 && r.height > 0 &&
                                       r.y >= 0 && r.y <= window.innerHeight;
                            }});

                            if (buttons.length === 0) continue;

                            // Pick rightmost small button (add buttons are usually on the right)
                            buttons.sort((a, b) => {{
                                const rA = a.getBoundingClientRect();
                                const rB = b.getBoundingClientRect();
                                const svgA = a.querySelector('svg') ? 20 : 0;
                                const svgB = b.querySelector('svg') ? 20 : 0;
                                return (rB.x + svgB) - (rA.x + svgA);
                            }});

                            const btn = buttons[0];
                            const r = btn.getBoundingClientRect();
                            return {{
                                x: r.x + r.width / 2,
                                y: r.y + r.height / 2,
                                scrollPos: {scroll_pos}
                            }};
                        }}
                    }}
                    return null;
                }}
            """)

            if result and result.get('x'):
                print(f"✅ Strategy 2 (scroll={scroll_pos}): Found button at ({result['x']:.0f}, {result['y']:.0f})")
                await page.mouse.click(result['x'], result['y'])
                await asyncio.sleep(2)
                # Scroll back to top
                await page.evaluate("window.scrollTo(0, 0)")
                return True

        # Reset scroll
        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(0.5)
    except Exception as e:
        print(f"⚠️ Strategy 2 (scroll search) failed: {e}")

    # ── Strategy 3: Search box first, then find the tool card ──
    # If there's a search/filter input, use it to narrow down results
    try:
        search_input = None
        for sel in [
            "input[placeholder*='search' i]:not([type='file'])",
            "input[type='search']",
            "input[placeholder*='filter' i]:not([type='file'])",
        ]:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                box = await el.bounding_box()
                viewport = page.viewport_size or {"height": 1080}
                if box and box["y"] < viewport["height"] * 0.7:
                    search_input = el
                    break

        if search_input:
            print(f"🔍 Strategy 3: Using search box to find '{tool_name}'")
            await search_input.click(timeout=3000)
            await asyncio.sleep(0.3)
            await search_input.fill(tool_name)
            await asyncio.sleep(2)

            # Now re-run Strategy 1 with filtered results
            result = await page.evaluate(f"""
                () => {{
                    const toolWords = {json.dumps(tool_words)};
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    let node;

                    while (node = walker.nextNode()) {{
                        const text = (node.textContent || '').toLowerCase().trim();
                        if (!toolWords.some(w => text.includes(w))) continue;

                        const textEl = node.parentElement;
                        if (!textEl) continue;
                        const textRect = textEl.getBoundingClientRect();
                        if (textRect.width === 0 || textRect.y < 60 || textRect.y > window.innerHeight) continue;

                        let container = textEl;
                        for (let d = 0; d < 8; d++) {{
                            container = container.parentElement;
                            if (!container || container === document.body) break;

                            const rect = container.getBoundingClientRect();
                            if (rect.width > window.innerWidth * 0.9) continue;

                            const buttons = Array.from(container.querySelectorAll(
                                'button, [role="button"]'
                            )).filter(btn => {{
                                const r = btn.getBoundingClientRect();
                                return r.width > 0 && r.width <= 80;
                            }});

                            if (buttons.length === 0) continue;

                            // Prefer SVG icon buttons (add buttons)
                            const svgBtns = buttons.filter(b => b.querySelector('svg'));
                            const target = svgBtns.length > 0 ? svgBtns[svgBtns.length - 1] : buttons[buttons.length - 1];
                            const r = target.getBoundingClientRect();
                            return {{ x: r.x + r.width/2, y: r.y + r.height/2 }};
                        }}
                    }}
                    return null;
                }}
            """)

            if result and result.get('x'):
                print(f"✅ Strategy 3: After search, found button at ({result['x']:.0f}, {result['y']:.0f})")
                await page.mouse.click(result['x'], result['y'])
                await asyncio.sleep(2)
                return True
    except Exception as e:
        print(f"⚠️ Strategy 3 failed: {e}")

    # ── Strategy 4: GPT-4o vision — last resort ──
    if openai_client and screenshot_b64:
        try:
            print("👁️ Strategy 4: GPT-4o vision fallback...")
            vision_response = await openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"Look at this screenshot. Find the small add/plus button "
                            f"that is directly associated with the tool card named '{tool_name}'. "
                            f"It's a small icon button (not a text button) inside or next to the '{tool_name}' card. "
                            f"Do NOT return the main page '+' button in a header/nav area. "
                            f"Return JSON: {{\"x\": N, \"y\": N, \"confidence\": \"high/medium/low\"}}"
                        )
                    },
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}}]
                    }
                ],
                max_tokens=100,
                response_format={"type": "json_object"}
            )
            data = json.loads(vision_response.choices[0].message.content)
            vx, vy = data.get("x", -1), data.get("y", -1)
            conf = data.get("confidence", "low")
            print(f"👁️ GPT-4o says: ({vx}, {vy}) confidence={conf}")

            if vx > 0 and vy > 0 and conf != "low":
                await page.mouse.click(vx, vy)
                await asyncio.sleep(2)
                return True
        except Exception as e:
            print(f"⚠️ Strategy 4 GPT-4o vision failed: {e}")

    print(f"❌ All strategies failed to find add button for '{tool_name}'")
    return False


async def try_click_add_button_smart(page, target_tool_name: str = None, openai_client=None, screenshot_b64: str = None) -> bool:
    """
    Entry point for clicking an add/plus button.
    If target_tool_name is given, finds the button INSIDE that tool's card.
    Otherwise falls back to finding a generic plus button (e.g. in a panel header).
    """
    if target_tool_name:
        return await click_add_button_for_tool(page, target_tool_name, openai_client, screenshot_b64)

    # Generic plus button (for panel headers, no specific tool)
    print(f"🔍 Generic '+' button search (no target tool)...")
    try:
        result = await page.evaluate("""
            () => {
                // Find the 'Agent Tools' header element
                let anchorEl = null;
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while (node = walker.nextNode()) {
                    const t = (node.textContent || '').trim().toLowerCase();
                    if ((t === 'agent tools' || t === 'agent tool') && t.length < 30) {
                        anchorEl = node.parentElement;
                        break;
                    }
                }
                if (!anchorEl) return null;

                let container = anchorEl;
                for (let i = 0; i < 6; i++) {
                    container = container.parentElement;
                    if (!container) break;
                    const cRect = container.getBoundingClientRect();
                    if (cRect.width > 100 && cRect.height < 80) {
                        const btns = Array.from(container.querySelectorAll('button, [role="button"]'));
                        let best = null;
                        for (const btn of btns) {
                            const r = btn.getBoundingClientRect();
                            if (r.width > 0 && r.width < 80 && r.height > 0 && r.height < 80) {
                                if (!best || r.x > best.x) {
                                    best = { x: r.x + r.width/2, y: r.y + r.height/2 };
                                }
                            }
                        }
                        if (best) return best;
                    }
                }
                return null;
            }
        """)

        if result and result.get('x'):
            print(f"✅ Generic '+': Found header row button at ({result['x']:.0f}, {result['y']:.0f})")
            await page.mouse.click(result['x'], result['y'])
            await asyncio.sleep(2.5)
            return True
    except Exception as e:
        print(f"⚠️ Generic '+' search failed: {e}")

    return False


# -------------------------------------------------
# SEARCH FOR TOOL INSIDE OPEN PANEL
# -------------------------------------------------

async def search_tool_in_panel(page, tool_name: str) -> bool:
    """
    After clicking '+', find and click the actual tool card.
    Excludes file inputs and chat boxes.
    """
    print(f"🔍 Waiting for tool selection panel to appear...")

    panel_appeared = False
    for _ in range(10):
        await asyncio.sleep(0.5)
        try:
            appeared = await page.evaluate("""
                () => {
                    const selectors = [
                        '[role="dialog"]', '[class*="modal"]', '[class*="drawer"]',
                        '[class*="sheet"]', '[class*="popup"]', '[class*="overlay"]',
                        '[class*="tool-select"]', '[class*="toolSelect"]',
                        '[class*="marketplace"]', '[class*="add-tool"]'
                    ];
                    for (const sel of selectors) {
                        for (const el of document.querySelectorAll(sel)) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 100 && rect.height > 100) return true;
                        }
                    }
                    return false;
                }
            """)
            if appeared:
                panel_appeared = True
                print(f"✅ Tool selection panel appeared in DOM!")
                break
        except:
            pass

    if not panel_appeared:
        print(f"⚠️ No modal/panel detected — proceeding anyway")

    print(f"🔍 Searching for tool '{tool_name}' inside panel...")
    await asyncio.sleep(1)

    search_selectors = [
        "input[placeholder*='search' i]:not([type='file']):not([type='hidden'])",
        "input[placeholder*='Search' i]:not([type='file']):not([type='hidden'])",
        "input[placeholder*='filter' i]:not([type='file'])",
        "input[type='search']",
        "[role='dialog'] input:not([type='file'])",
        "[class*='modal'] input:not([type='file'])",
        "[class*='drawer'] input:not([type='file'])",
        "[class*='marketplace'] input:not([type='file'])",
    ]

    search_input = None
    for sel in search_selectors:
        try:
            el = page.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                input_type = await el.get_attribute("type") or ""
                if input_type.lower() == "file":
                    continue
                box = await el.bounding_box()
                viewport = page.viewport_size or {"height": 1080}
                if box and box["y"] > viewport["height"] * 0.75:
                    print(f"⚠️ Skipping input at bottom of page (likely chat box): y={box['y']:.0f}")
                    continue
                search_input = el
                print(f"✅ Found search input: {sel}")
                break
        except:
            continue

    if search_input:
        try:
            await search_input.click(timeout=3000)
            await asyncio.sleep(0.3)
            await search_input.fill(tool_name)
            print(f"✅ Typed '{tool_name}' in search box")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠️ Could not type in search box: {e}")

    tool_name_lower = tool_name.lower()
    tool_words = tool_name_lower.split()

    try:
        result = await page.evaluate(f"""
            () => {{
                const toolName = {json.dumps(tool_name_lower)};
                const toolWords = {json.dumps(tool_words)};
                const viewport = {{ width: window.innerWidth, height: window.innerHeight }};

                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                const matches = [];
                let node;

                while (node = walker.nextNode()) {{
                    const text = (node.textContent || "").toLowerCase().trim();
                    if (toolWords.some(w => w.length > 2 && text.includes(w))) {{
                        const el = node.parentElement;
                        if (!el) continue;
                        const tagName = el.tagName.toLowerCase();
                        if (tagName === "input" || tagName === "textarea") continue;
                        const rect = el.getBoundingClientRect();
                        if (rect.y > viewport.height * 0.75) continue;
                        if (rect.width === 0 || rect.height === 0) continue;
                        const isCard = rect.width > 50 && rect.width < 600 && rect.height > 20 && rect.height < 200;
                        if (!isCard) continue;
                        const score = rect.x / viewport.width;
                        matches.push({{
                            x: rect.x + rect.width / 2,
                            y: rect.y + rect.height / 2,
                            score: score,
                            text: text.substring(0, 50),
                            tag: tagName
                        }});
                    }}
                }}

                matches.sort((a, b) => b.score - a.score);
                return matches.slice(0, 5);
            }}
        """)

        if result and len(result) > 0:
            print(f"📋 Tool card candidates: {[(r.get('x',0), r.get('y',0), r.get('text','')[:30]) for r in result]}")
            best = result[0]
            print(f"✅ Clicking tool card at ({best['x']:.0f}, {best['y']:.0f})")
            await page.mouse.click(best['x'], best['y'])
            await asyncio.sleep(2)
            await asyncio.sleep(1)
            tool_added = await page.evaluate(f"""
                () => {{
                    const toolName = {json.dumps(tool_name_lower)};
                    const allText = document.body.innerText.toLowerCase();
                    if (allText.includes("no agent tools added")) return false;
                    return true;
                }}
            """)
            if tool_added:
                print(f"✅ Tool addition confirmed!")
                return True
            else:
                print(f"⚠️ 'No Agent Tools Added' still showing")
    except Exception as e:
        print(f"⚠️ JS card search failed: {e}")

    card_selectors = [
        f"[class*='tool']:has-text('{tool_name}')",
        f"[class*='card']:has-text('{tool_name}')",
        f"[class*='item']:has-text('{tool_name}')",
        f"li:has-text('{tool_name}')",
        f"div[role='button']:has-text('{tool_name}')",
        f"button:has-text('{tool_name}')",
    ]

    for sel in card_selectors:
        try:
            els = page.locator(sel)
            count = await els.count()
            for i in range(count):
                el = els.nth(i)
                if not await el.is_visible():
                    continue
                tag = await el.evaluate("el => el.tagName.toLowerCase()")
                if tag in ["input", "textarea"]:
                    continue
                box = await el.bounding_box()
                viewport = page.viewport_size or {"height": 1080}
                if box and box["y"] > viewport["height"] * 0.75:
                    continue
                print(f"✅ Found tool card via: {sel}")
                await el.scroll_into_view_if_needed(timeout=3000)
                await el.click(force=True, timeout=5000)
                print(f"✅ Clicked tool '{tool_name}'!")
                await asyncio.sleep(2)
                return True
        except:
            continue

    print(f"❌ Could not find tool '{tool_name}' in panel")
    return False


async def try_find_and_click_tool(page, tool_name: str) -> bool:
    print(f"🔍 Searching for tool: '{tool_name}'")
    return await search_tool_in_panel(page, tool_name)


def extract_tool_name_from_prompt(prompt: str) -> str | None:
    """
    Dynamically extract the TOOL name the user wants to add.
    """
    prompt_lower = prompt.lower()

    skip_phrases = [
        'ondemand ai agents', 'on-demand', 'ondemand', 'the website',
        'the platform', 'the playground', 'the page'
    ]

    quoted = _re.findall(r"[\'\"]([\^\'\"]{2,40})[\'\"]", prompt)
    for q in quoted:
        ql = q.lower()
        if any(skip in ql for skip in skip_phrases):
            continue
        if any(word in ql for word in ['video', 'search', 'gen', 'perplexity',
                                        'linkedin', 'xai', 'dall', 'stable', 'image',
                                        'weather', 'wolfram', 'calculator', 'code', 'gpt']):
            return q

    known_tools = [
        'gpt search', 'xai video', 'perplexity', 'linkedin', 'web search',
        'image gen', 'dalle', 'stable diffusion', 'google search',
        'bing search', 'wikipedia', 'wolfram', 'calculator', 'code interpreter'
    ]
    for tool in known_tools:
        if tool in prompt_lower:
            return tool

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

    last_b64_image = None
    consecutive_failures = 0
    last_action = None
    last_label = None
    action_repeat_count = 0
    blocker_detected = False

    tools_panel_open = False
    plus_click_attempts = 0

    extracted_contents = []

    extract_content = any(keyword in request.prompt.lower() for keyword in [
        'extract', 'get content', 'scrape', 'fetch content', 'get text',
        'get images', 'capture content', 'save content', 'read content',
        'content from', 'text from', 'images from'
    ])

    if extract_content:
        print("📦 Content extraction ENABLED")
    else:
        print("⚡ Content extraction DISABLED")

    target_tool_name = extract_tool_name_from_prompt(request.prompt)
    if target_tool_name:
        print(f"🎯 Target tool detected from prompt: '{target_tool_name}'")
    else:
        print("ℹ️ No specific tool name detected in prompt")

    try:
        async with async_playwright() as p:

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
                    "--disable-gpu",
                    "--window-size=1920,1080",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding"
                ]
            )

            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="America/New_York",
                permissions=["geolocation", "notifications"],
                geolocation={"latitude": 40.7128, "longitude": -74.0060},
                color_scheme="light",
                device_scale_factor=1,
                has_touch=False,
                is_mobile=False,
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Cache-Control": "max-age=0"
                }
            )

            page = await context.new_page()
            await apply_ultimate_stealth(page)

            print(f"🚀 Starting Task: {request.prompt}")

            prompt_lower = request.prompt.lower()
            search_query = None

            if any(word in prompt_lower for word in ["search", "google", "find", "look for"]):
                try:
                    extract_response = await client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "Extract ONLY the search query from the user's request. "
                                    "Remove all instructions like 'go to', 'search for', 'google', 'find', etc. "
                                    "Return ONLY the actual search terms.\n\n"
                                    "Examples:\n"
                                    "Input: 'go to google.com search for man utd and open any article by cnn'\n"
                                    "Output: man utd cnn\n\n"
                                    "Input: 'search for latest iPhone 15 reviews'\n"
                                    "Output: latest iPhone 15 reviews\n\n"
                                    "Return ONLY the search query, nothing else."
                                )
                            },
                            {"role": "user", "content": request.prompt}
                        ],
                        max_tokens=50
                    )
                    content = extract_response.choices[0].message.content
                    search_query = content.strip() if content else None
                    if search_query:
                        print(f"🔍 Extracted search query: '{search_query}'")
                except Exception as e:
                    print(f"⚠️ Query extraction failed: {e}")
                    search_query = None

            if search_query:
                print(f"🎯 Using Brave Search for: {search_query}")
                await smart_brave_search(page, search_query)
            else:
                start_url = get_smart_start_url(request.prompt)
                print(f"🎯 Smart routing to: {start_url}")
                await page.goto(start_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

            # ─── MAIN LOOP ───
            for step in range(1, 51):

                try:
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.2)
                except:
                    pass

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
                    print(f"🛑 Captcha could not be solved: {captcha_reason}")
                    final_message = f"Failed: {captcha_reason}"
                    final_status = "failed"
                    blocker_detected = True
                    break

                if step % 3 == 0 or consecutive_failures > 2:
                    task_involves_login = any(word in request.prompt.lower() for word in
                                              ['login', 'sign in', 'log in', 'signin'])
                    if not task_involves_login:
                        print(f"🔍 Checking for blocking popups at step {step}...")
                        blocker_check = await detect_blocking_elements(page, last_b64_image, client)
                        if blocker_check.get("blocked"):
                            blocker_type = blocker_check.get("blocker_type")
                            reason = blocker_check.get("reason")
                            print(f"🚨 BLOCKER DETECTED: {blocker_type} - {reason}")
                            if blocker_type == "login":
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
                                print("🔧 Attempting to accept cookies...")
                                await attempt_popup_bypass(page)
                                await page.wait_for_timeout(1000)
                                continue
                    else:
                        print(f"⏭️ Skipping blocker check - task involves login")

                user_instructions = ""
                dont_matches = _re.findall(r"don'?t[^.!?]*[.!?]", request.prompt, _re.IGNORECASE)
                if dont_matches:
                    user_instructions = "⚠️ CRITICAL USER INSTRUCTIONS:\n"
                    for instruction in dont_matches:
                        user_instructions += f"- {instruction.strip()}\n"
                    user_instructions += "\n"

                panel_context = ""
                if tools_panel_open:
                    panel_context = (
                        "🔧 TOOLS PANEL IS CURRENTLY OPEN.\n"
                        f"You should now look in the panel for the tool '{target_tool_name or 'requested tool'}'.\n"
                        "Do NOT click '+' again — the panel is already open.\n"
                        "If you see a search box inside the panel, type the tool name.\n"
                        "Then click the tool card/item that matches.\n\n"
                    )

                system_prompt = (
                    f"ORIGINAL TASK: {request.prompt}\n\n"
                    f"{user_instructions}"
                    f"{panel_context}"
                    f"CURRENT STEP: {step}/50\n\n"
                    "=== YOUR JOB (2-STEP PROCESS) ===\n\n"
                    "STEP 1 - ANALYZE CURRENT STATE:\n"
                    "Look at the screenshot and describe:\n"
                    "1. What page am I on?\n"
                    "2. What elements are visible?\n"
                    "3. What has been completed so far?\n"
                    "4. What still needs to be done?\n\n"
                    "STEP 2 - DECIDE NEXT ACTION:\n"
                    "- If on marketplace/tools list page → look for the specific tool card and its add button\n"
                    "- If tools panel is open → look for the tool name or a search box inside panel\n"
                    "- If tool found in panel → click it\n"
                    "- If search box in panel → type tool name into it first\n"
                    "- If '+' button needs to be clicked to open tools panel → click '+'\n"
                    "- If a tool was just added → look for conversational starters and click the first one\n"
                    "- If task says 'run first query' → click the first conversational starter/suggested query\n\n"
                    "🔑 CRITICAL LOGIN FLOW RULES:\n"
                    "- If on login page with ONLY email field: type email → click Continue\n"
                    "- If email FILLED and Continue button visible: CLICK Continue (DO NOT retype)\n"
                    "- If on password page: type password → click Continue/Login\n"
                    "- NEVER type same email/password multiple times!\n\n"
                    "🚫 ANTI-LOOP RULES:\n"
                    "- If you just clicked '+' and a panel/modal appeared → do NOT click '+' again\n"
                    "- After panel opens, search for the tool NAME and click it directly\n"
                    "- Do NOT keep clicking the same element repeatedly\n"
                    "- Do NOT click 'Browse Marketplace' more than once\n\n"
                    "🚫 ANTI-DISTRACTION RULES:\n"
                    "- Ignore flashcards and promotional suggestions unless explicitly asked\n"
                    "- Focus ONLY on elements needed to complete the task\n\n"
                    "⚠️ IMPORTANT FOR MARKETPLACE PAGE:\n"
                    "- When on a marketplace/tools list, use label='+ for [ToolName]' to signal clicking the add button next to a specific tool\n"
                    "- The system will find and click the correct button automatically\n\n"
                    "RESPONSE FORMAT (JSON ONLY):\n"
                    "{\n"
                    "  \"current_state\": \"Brief description of what's on screen\",\n"
                    "  \"completed_steps\": \"What parts of task are done\",\n"
                    "  \"remaining_steps\": \"What still needs to be done\",\n"
                    "  \"action\": \"click\" or \"type\" or \"done\",\n"
                    "  \"label\": \"SHORT visible text on button/link (5-10 words max)\",\n"
                    "  \"text_to_type\": \"text to enter (if action is type)\",\n"
                    "  \"reason\": \"why this is the next logical action\"\n"
                    "}\n"
                )

                if consecutive_failures > 3:
                    system_prompt += (
                        f"\n⚠️ WARNING: {consecutive_failures} consecutive failures.\n"
                        "Try a DIFFERENT approach - different selector, scroll down, or explain blocker.\n"
                    )

                try:
                    response = await client.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {
                                "role": "user",
                                "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{last_b64_image}"}}]
                            }
                        ],
                        max_tokens=300,
                        response_format={"type": "json_object"}
                    )

                    content = response.choices[0].message.content
                    if not content or content.strip() == "":
                        print(f"⚠️ GPT-4o returned empty response, retrying...")
                        await asyncio.sleep(2)
                        continue

                    decision = json.loads(content)

                    print(f"🔍 STATE ANALYSIS:")
                    print(f"   Current: {decision.get('current_state', 'Unknown')}")
                    print(f"   Done: {decision.get('completed_steps', 'Unknown')}")
                    print(f"   ToDo: {decision.get('remaining_steps', 'Unknown')}")

                except json.JSONDecodeError as e:
                    print(f"⚠️ JSON decode error: {e}")
                    decision = {"action": "done", "reason": "JSON parsing failed"}
                except Exception as e:
                    print(f"⚠️ GPT-4o error: {e}")
                    await asyncio.sleep(2)
                    continue

                current_action = decision.get('action', 'unknown')
                current_label = decision.get('label', '')
                reason = decision.get('reason', 'No reason provided')

                print(f"📍 Step {step} (Tab {len(all_pages)}): {current_action} '{current_label}' -> {reason}")

                action_signature = f"{current_action}:{current_label}"
                if action_signature == f"{last_action}:{last_label}":
                    action_repeat_count += 1
                else:
                    action_repeat_count = 0
                    last_action = current_action
                    last_label = current_label

                if action_repeat_count >= 7:
                    print(f"🛑 Same action '{action_signature}' repeated {action_repeat_count} times. Giving up.")
                    final_message = f"Failed: Stuck in loop on '{current_action}:{current_label}'"
                    break

                if decision['action'] == 'done':
                    reason_lower = reason.lower()
                    if any(word in reason_lower for word in ["blocked", "robot", "login"]) and "captcha" not in reason_lower:
                        final_message = f"Failed: {reason}"
                        final_status = "failed"
                        blocker_detected = True
                        break

                    print(f"🔍 Agent claims task is done. Verifying with LLM...")
                    try:
                        final_verification = await client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[
                                {
                                    "role": "system",
                                    "content": (
                                        f"ORIGINAL TASK: {request.prompt}\n\n"
                                        "Look at this screenshot and determine if the task is COMPLETELY finished.\n\n"
                                        "Return ONLY:\n"
                                        "- 'COMPLETE' if ALL steps are done and visible on screen\n"
                                        "- 'INCOMPLETE: [what's missing]' if any step is not done\n"
                                        "- 'BLOCKED: [blocker type]' if there's a login/captcha/error blocking progress"
                                    )
                                },
                                {
                                    "role": "user",
                                    "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{last_b64_image}"}}]
                                }
                            ],
                            max_tokens=100
                        )
                        verification_result = final_verification.choices[0].message.content or "COMPLETE"
                        print(f"🔍 Verification result: {verification_result}")

                        if "COMPLETE" in verification_result.upper():
                            print("✅ Verification PASSED!")
                        elif "BLOCKED" in verification_result.upper():
                            print("🚫 Verification found blocker")
                            final_message = f"Failed: {verification_result}"
                            final_status = "failed"
                            blocker_detected = True
                            break
                        else:
                            print(f"❌ Verification FAILED: {verification_result}")
                            consecutive_failures += 1
                            if action_repeat_count >= 3:
                                final_message = f"Failed: Agent completed task incorrectly - {verification_result}"
                                final_status = "failed"
                                break
                            continue

                    except Exception as e:
                        print(f"⚠️ Final verification failed: {e}")

                    if step < 4:
                        try:
                            verify_response = await client.chat.completions.create(
                                model="gpt-4o-mini",
                                messages=[
                                    {
                                        "role": "system",
                                        "content": f"Look at this screenshot. Task was: '{request.prompt}'. Is task COMPLETELY finished? Answer 'YES' or 'NO' with 1 sentence."
                                    },
                                    {
                                        "role": "user",
                                        "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{last_b64_image}"}}]
                                    }
                                ],
                                max_tokens=100
                            )
                            verification = verify_response.choices[0].message.content or "YES"
                            print(f"🔍 Early-finish check: {verification}")
                            if "NO" in verification.upper():
                                consecutive_failures += 1
                                continue
                        except:
                            pass

                    print("📸 Task complete! Capturing final screenshot...")
                    await asyncio.sleep(3)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except:
                        pass

                    final_b64_image = await get_b64_screenshot(page)
                    final_img_path = f"{folder_name}/step_{step + 1}_FINAL.png"
                    with open(final_img_path, "wb") as f:
                        f.write(base64.b64decode(final_b64_image))

                    print(f"✅ Final screenshot saved")
                    final_message = f"Success: {reason}"
                    final_status = "success"
                    break

                action_succeeded = False

                try:
                    if decision['action'] == 'click':
                        label = decision.get('label', '').strip()
                        print(f"🎯 Attempting to click: '{label}'")

                        # ── Detect if this is a "add tool" click on a marketplace/list page ──
                        # GPT will say things like "+ for GPT Search", "add GPT Search", "+ next to GPT Search"
                        # We intercept these and use our smart card-finder instead
                        is_add_tool_click = (
                            label in ['+', "'+'", '"+"'] or
                            _re.search(r'\+.*for\s+\w', label, _re.IGNORECASE) or
                            _re.search(r'add\s+\w', label, _re.IGNORECASE) or
                            _re.search(r'\+.*next\s+to', label, _re.IGNORECASE) or
                            _re.search(r'\+.*\(', label, _re.IGNORECASE) or
                            (target_tool_name and target_tool_name.lower() in label.lower() and '+' in label)
                        )

                        if is_add_tool_click:
                            print(f"🔧 Detected tool-add click pattern: '{label}'")

                            # If we know the tool name, go straight to card-based clicking
                            tool_to_add = target_tool_name

                            # Also try to extract tool name from the label itself
                            if not tool_to_add:
                                label_clean = _re.sub(r'[+\(\)\'"]+', '', label).strip()
                                for noise in ['for', 'next to', 'add', 'click', 'button']:
                                    label_clean = label_clean.replace(noise, '').strip()
                                if len(label_clean) > 2:
                                    tool_to_add = label_clean

                            if tool_to_add:
                                # Snapshot DOM before
                                before_snap = await page.evaluate("""
                                    () => ({
                                        totalElements: document.querySelectorAll('*').length,
                                        bodyText: document.body.innerText.toLowerCase().substring(0, 500)
                                    })
                                """)

                                clicked = await click_add_button_for_tool(
                                    page, tool_to_add, openai_client=client, screenshot_b64=last_b64_image
                                )

                                if clicked:
                                    action_succeeded = True
                                    action_repeat_count = 0
                                    await asyncio.sleep(2)

                                    # Check if tool was added (page state changed)
                                    after_snap = await page.evaluate("""
                                        () => ({
                                            totalElements: document.querySelectorAll('*').length,
                                            bodyText: document.body.innerText.toLowerCase().substring(0, 500)
                                        })
                                    """)

                                    dom_changed = abs(after_snap['totalElements'] - before_snap['totalElements']) > 3
                                    if dom_changed:
                                        print(f"✅ DOM changed after add-button click — tool likely added!")
                                        tools_panel_open = False
                                        plus_click_attempts = 0
                                    else:
                                        print(f"⚠️ DOM didn't change much — may need another click")
                                        plus_click_attempts += 1
                                else:
                                    plus_click_attempts += 1
                                    print(f"⚠️ Add button not found (attempt {plus_click_attempts})")

                            else:
                                # Pure '+' with no tool name — generic panel open
                                before_snap = await page.evaluate("""
                                    () => ({
                                        totalElements: document.querySelectorAll('*').length,
                                        visibleInputs: document.querySelectorAll('input:not([type="hidden"]):not([type="file"])').length,
                                        overlays: document.querySelectorAll(
                                            '[role="dialog"],[data-state="open"],[data-open="true"],' +
                                            'div[class*="modal"],div[class*="drawer"],div[class*="sheet"],' +
                                            'div[class*="popup"],div[class*="overlay"],div[class*="panel"]'
                                        ).length
                                    })
                                """)

                                plus_clicked = await try_click_add_button_smart(
                                    page,
                                    target_tool_name=None,
                                    openai_client=client,
                                    screenshot_b64=last_b64_image
                                )

                                if plus_clicked:
                                    action_succeeded = True
                                    action_repeat_count = 0
                                    await asyncio.sleep(2.5)

                                    after_snap = await page.evaluate("""
                                        () => ({
                                            totalElements: document.querySelectorAll('*').length,
                                            visibleInputs: document.querySelectorAll('input:not([type="hidden"]):not([type="file"])').length,
                                            overlays: document.querySelectorAll(
                                                '[role="dialog"],[data-state="open"],[data-open="true"],' +
                                                'div[class*="modal"],div[class*="drawer"],div[class*="sheet"],' +
                                                'div[class*="popup"],div[class*="overlay"],div[class*="panel"]'
                                            ).length
                                        })
                                    """)

                                    dom_grew = after_snap['totalElements'] > before_snap['totalElements'] + 5
                                    new_inputs = after_snap['visibleInputs'] > before_snap['visibleInputs']
                                    new_overlays = after_snap['overlays'] > before_snap['overlays']
                                    panel_opened = dom_grew or new_inputs or new_overlays

                                    print(f"📊 DOM change: elements +{after_snap['totalElements'] - before_snap['totalElements']}, "
                                          f"panel_opened={panel_opened}")

                                    if panel_opened:
                                        tools_panel_open = True
                                        plus_click_attempts = 0
                                        if target_tool_name:
                                            tool_found = await search_tool_in_panel(page, target_tool_name)
                                            if tool_found:
                                                tools_panel_open = False
                                                action_repeat_count = 0
                                    else:
                                        plus_click_attempts += 1
                                else:
                                    plus_click_attempts += 1

                            if plus_click_attempts >= 5:
                                print("🛑 Cannot find add button after 5 attempts — stopping task")
                                final_message = "Failed: Could not locate the tool add button after 5 attempts"
                                final_status = "failed"
                                break

                        # ── CASE: Tool name click when panel is open ──
                        elif tools_panel_open:
                            print(f"🔧 Panel open — searching for tool '{label}'...")
                            tool_clicked = await try_find_and_click_tool(page, label)
                            if tool_clicked:
                                action_succeeded = True
                                tools_panel_open = False
                                action_repeat_count = 0
                                print(f"✅ Tool '{label}' selected from panel!")

                        # ── CASE: Normal click logic ──
                        if not action_succeeded and not is_add_tool_click:
                            element = None
                            found_method = None

                            if label.lower() == "add to cart":
                                try:
                                    element = page.locator("#add-to-cart-button, input[name='submit.add-to-cart'], button:has-text('Add to Cart'):visible").first
                                    if await element.count() > 0 and await element.is_visible():
                                        found_method = "Amazon Add to cart"
                                except:
                                    pass

                            if not found_method:
                                try:
                                    element = page.get_by_role("link", name=label).first
                                    if await element.count() > 0:
                                        found_method = "Exact link match"
                                except:
                                    pass

                            if not found_method:
                                try:
                                    element = page.get_by_role("button", name=label).first
                                    if await element.count() > 0:
                                        found_method = "Exact button match"
                                except:
                                    pass

                            if not found_method:
                                try:
                                    partial_label = ' '.join(label.split()[:5])
                                    element = page.get_by_role("link").filter(has_text=partial_label).first
                                    if await element.count() > 0:
                                        found_method = "Partial link match"
                                except:
                                    pass

                            if not found_method:
                                try:
                                    shorter_label = ' '.join(label.split()[:3])
                                    element = page.get_by_role("link").filter(has_text=shorter_label).first
                                    if await element.count() > 0:
                                        found_method = "Short partial match"
                                except:
                                    pass

                            if not found_method:
                                try:
                                    element = page.locator("a, button").filter(has_text=label).first
                                    if await element.count() > 0:
                                        found_method = "General element match"
                                except:
                                    pass

                            if not found_method:
                                try:
                                    element = page.get_by_text(label, exact=False).first
                                    if await element.count() > 0:
                                        found_method = "Broad text match"
                                except:
                                    pass

                            if not found_method and ("amazon" in label.lower() or "iphone" in label.lower() or "product" in label.lower()):
                                try:
                                    element = page.locator("a[href*='amazon.com'][href*='dp/'], a[href*='amazon.com'][href*='gp/']").first
                                    if await element.count() > 0:
                                        found_method = "First Amazon product link"
                                    else:
                                        element = page.locator("a[href*='amazon']").first
                                        if await element.count() > 0:
                                            found_method = "Any Amazon link"
                                except:
                                    pass

                            if found_method and element and await element.count() > 0:
                                print(f"✅ Found element using: {found_method}")
                                click_attempts = 0
                                while click_attempts < 3 and not action_succeeded:
                                    click_attempts += 1
                                    if click_attempts > 1:
                                        print(f"🔄 Retry attempt {click_attempts}/3")

                                    try:
                                        await element.scroll_into_view_if_needed(timeout=3000)
                                        await asyncio.sleep(0.3)
                                    except:
                                        pass

                                    try:
                                        await element.hover(timeout=3000)
                                        await asyncio.sleep(0.3)
                                    except:
                                        pass

                                    try:
                                        await element.click(timeout=5000, force=True)
                                        await asyncio.sleep(2)
                                        print(f"✅ Click executed (attempt {click_attempts})")

                                        try:
                                            still_there = await element.count() > 0
                                            if still_there and click_attempts < 3:
                                                is_visible = await element.is_visible()
                                                if is_visible:
                                                    await asyncio.sleep(1)
                                                    try:
                                                        await page.keyboard.press("Escape")
                                                        await asyncio.sleep(0.5)
                                                    except:
                                                        pass
                                                    continue
                                                else:
                                                    action_succeeded = True
                                            else:
                                                action_succeeded = True
                                        except:
                                            action_succeeded = True

                                    except Exception as click_err:
                                        print(f"❌ Click failed (attempt {click_attempts}): {click_err}")
                                        if click_attempts < 3:
                                            await asyncio.sleep(1)

                                if action_succeeded:
                                    print(f"✅ Click completed successfully!")
                                    await asyncio.sleep(2)

                                    for dismiss_sel in ["button[aria-label*='Close']", "button[aria-label*='Dismiss']",
                                                        "button:has-text('×')", "button:has-text('Close')",
                                                        "button:has-text('Got it')", "button:has-text('OK')",
                                                        "button.close", "[data-dismiss]"]:
                                        try:
                                            dismiss_btn = page.locator(dismiss_sel).first
                                            if await dismiss_btn.count() > 0 and await dismiss_btn.is_visible():
                                                await dismiss_btn.click(timeout=2000)
                                                await asyncio.sleep(0.5)
                                                break
                                        except:
                                            continue

                                    if "amazon" in page.url.lower():
                                        await asyncio.sleep(2)
                                        for sel in ["button[data-action='a-popover-close']", "a.a-popover-close",
                                                    "button.a-button-close", "button:has-text('Not now')",
                                                    "button:has-text('Skip')", "a:has-text('Skip sign in')",
                                                    "[aria-label='Close']"]:
                                            try:
                                                btn = page.locator(sel).first
                                                if await btn.count() > 0 and await btn.is_visible():
                                                    await btn.click(timeout=2000, force=True)
                                                    await asyncio.sleep(1)
                                                    break
                                            except:
                                                continue

                                    if extract_content:
                                        try:
                                            await page.wait_for_load_state("domcontentloaded", timeout=3000)
                                            await asyncio.sleep(1)
                                            page_content = await extract_page_content(page)
                                            if page_content and page_content.get("text"):
                                                extracted_contents.append({
                                                    "step": step,
                                                    "action": f"Clicked: {label}",
                                                    "content": page_content
                                                })
                                        except Exception as e:
                                            print(f"⚠️ Content extraction skipped: {e}")
                            else:
                                print(f"❌ Could not find clickable element for: '{label}'")

                    elif decision['action'] == 'type':
                        text_to_type = decision.get('text_to_type', '')

                        is_password = (
                            any(word in decision.get('reason', '').lower() for word in ['password', 'pass']) or
                            'password' in text_to_type.lower() or
                            (len(text_to_type) >= 6 and any(c in text_to_type for c in ['@', '!', '#', '$', '%']))
                        )
                        is_email = '@' in text_to_type and '.' in text_to_type

                        print(f"📝 Typing: {text_to_type[:20]}... (password={is_password}, email={is_email})")

                        input_element = None
                        input_found = False

                        if is_password:
                            try:
                                input_element = page.locator(
                                    "input[type='password']:visible, input[name='password']:visible, input[placeholder*='password' i]:visible"
                                ).first
                                if await input_element.count() > 0 and await input_element.is_visible():
                                    input_found = True
                            except:
                                pass

                        if not input_found and is_email:
                            try:
                                input_element = page.locator(
                                    "input[type='email']:visible, input[name='email']:visible, input[name='username']:visible, input[placeholder*='email' i]:visible"
                                ).first
                                if await input_element.count() > 0 and await input_element.is_visible():
                                    input_found = True
                            except:
                                pass

                        if not input_found and not is_password and not is_email:
                            try:
                                input_element = page.locator(
                                    "input[type='search']:visible, [aria-label='Search']:visible"
                                ).first
                                if await input_element.count() > 0 and await input_element.is_visible():
                                    input_found = True
                            except:
                                pass

                        if not input_found:
                            try:
                                input_element = page.locator(
                                    "input:visible:not([type='hidden']):not([type='submit']):not([type='button']):not([type='file'])"
                                ).first
                                if await input_element.count() > 0 and await input_element.is_visible():
                                    current_value = await input_element.input_value()
                                    if not current_value or len(current_value.strip()) == 0:
                                        input_found = True
                                    else:
                                        print(f"⚠️ Input already has value: {current_value[:20]}...")
                            except:
                                pass

                        if not input_found:
                            try:
                                input_element = page.locator(
                                    "input:visible:not([type='file']), textarea:visible"
                                ).first
                                if await input_element.count() > 0:
                                    input_found = True
                            except:
                                pass

                        if input_found:
                            try:
                                await input_element.click(timeout=5000)
                                await asyncio.sleep(0.5)
                                await input_element.fill(text_to_type)
                                print(f"✅ Filled input with: {text_to_type[:20]}...")

                                input_type = await input_element.get_attribute('type')
                                input_name = await input_element.get_attribute('name')

                                has_continue_button = False
                                for cont_sel in ["button:has-text('Continue')", "button:has-text('Next')",
                                                 "button:has-text('Submit')", "button[type='submit']",
                                                 "input[type='submit']"]:
                                    try:
                                        cont_btn = page.locator(cont_sel).first
                                        if await cont_btn.count() > 0 and await cont_btn.is_visible():
                                            has_continue_button = True
                                            print(f"ℹ️ Continue/Submit button detected")
                                            break
                                    except:
                                        pass

                                if input_type in ['search', 'text'] and not has_continue_button and 'email' not in str(input_name).lower():
                                    await page.keyboard.press("Enter")
                                    print(f"✅ Pressed Enter")

                                await page.wait_for_timeout(2000)
                                action_succeeded = True
                            except Exception as e:
                                print(f"❌ Failed to fill input: {e}")
                                action_succeeded = False
                        else:
                            print(f"❌ Could not find any input field")
                            action_succeeded = False

                        if extract_content:
                            try:
                                await page.wait_for_load_state("domcontentloaded", timeout=3000)
                                page_content = await extract_page_content(page)
                                if page_content and page_content.get("text"):
                                    extracted_contents.append({
                                        "step": step,
                                        "action": f"Searched: {decision.get('text_to_type', '')}",
                                        "content": page_content
                                    })
                            except Exception as e:
                                print(f"⚠️ Search results extraction skipped: {e}")

                except Exception as ex:
                    print(f"⚠️ Action error: {ex}")
                    action_succeeded = False

                if action_succeeded:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1

                if consecutive_failures >= 8:
                    print(f"🛑 {consecutive_failures} consecutive failures. Stopping.")
                    final_message = f"Failed: Too many consecutive failures ({consecutive_failures})"
                    break

            if final_status == "failed" and last_b64_image and not blocker_detected:
                print("🤔 Task failed. Analyzing final screenshot...")
                error_reason = await analyze_failure(client, request.prompt, last_b64_image)
                final_message = f"Failed: {error_reason}"

            await browser.close()

            print("🏁 Generating video proof...")
            video_url = await create_and_upload_video(folder_name, session_id)
            print(f"✅ Video URL: {video_url}")

            content_summary = None
            if extracted_contents:
                content_summary = {"total_pages": len(extracted_contents), "pages": extracted_contents}

            return {
                "status": final_status,
                "result": final_message,
                "video_url": video_url,
                "extracted_content": content_summary
            }

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        video_url = await create_and_upload_video(folder_name, session_id)
        content_summary = None
        if extracted_contents:
            content_summary = {"total_pages": len(extracted_contents), "pages": extracted_contents}
        return {
            "status": "error",
            "result": f"System Error: {str(e)}",
            "video_url": video_url,
            "extracted_content": content_summary
        }

# Run with: python -m uvicorn main:app --reload
