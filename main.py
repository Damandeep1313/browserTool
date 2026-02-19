import os
import base64
import json
import asyncio
import uuid
import subprocess
import shutil
import urllib.parse
import httpx
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
    extracted_content: dict | None = None

# -------------------------------------------------
# 4. CAPSOLVER INTEGRATION
# -------------------------------------------------

async def solve_cloudflare_turnstile(page_url: str, site_key: str) -> dict:
    """Solve Cloudflare Turnstile using CapSolver"""
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
                print(f"❌ CapSolver create task error: {create_data.get('errorDescription')}")
                return {"success": False, "error": create_data.get('errorDescription')}
            
            task_id = create_data.get("taskId")
            print(f"✅ CapSolver task created: {task_id}")
            
            for attempt in range(60):
                await asyncio.sleep(2)
                
                result_response = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={
                        "clientKey": CAPSOLVER_API_KEY,
                        "taskId": task_id
                    },
                    timeout=30.0
                )
                
                result_data = result_response.json()
                
                if result_data.get("status") == "ready":
                    print(f"✅ CapSolver: Cloudflare Turnstile solved!")
                    return {
                        "success": True,
                        "solution": result_data.get("solution", {}).get("token")
                    }
                elif result_data.get("status") == "failed":
                    print(f"❌ CapSolver failed: {result_data.get('errorDescription')}")
                    return {"success": False, "error": result_data.get('errorDescription')}
                
                if attempt % 10 == 0:
                    print(f"⏳ CapSolver: Still solving... ({attempt * 2}s)")
            
            return {"success": False, "error": "Timeout waiting for solution"}
            
    except Exception as e:
        print(f"❌ CapSolver exception: {e}")
        return {"success": False, "error": str(e)}

async def solve_recaptcha_v2(page_url: str, site_key: str) -> dict:
    """Solve reCAPTCHA v2 using CapSolver"""
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
                print(f"❌ CapSolver create task error: {create_data.get('errorDescription')}")
                return {"success": False, "error": create_data.get('errorDescription')}
            
            task_id = create_data.get("taskId")
            print(f"✅ CapSolver task created: {task_id}")
            
            for attempt in range(60):
                await asyncio.sleep(2)
                
                result_response = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={
                        "clientKey": CAPSOLVER_API_KEY,
                        "taskId": task_id
                    },
                    timeout=30.0
                )
                
                result_data = result_response.json()
                
                if result_data.get("status") == "ready":
                    print(f"✅ CapSolver: reCAPTCHA v2 solved!")
                    return {
                        "success": True,
                        "solution": result_data.get("solution", {}).get("gRecaptchaResponse")
                    }
                elif result_data.get("status") == "failed":
                    print(f"❌ CapSolver failed: {result_data.get('errorDescription')}")
                    return {"success": False, "error": result_data.get('errorDescription')}
                
                if attempt % 10 == 0:
                    print(f"⏳ CapSolver: Still solving... ({attempt * 2}s)")
            
            return {"success": False, "error": "Timeout waiting for solution"}
            
    except Exception as e:
        print(f"❌ CapSolver exception: {e}")
        return {"success": False, "error": str(e)}

async def solve_hcaptcha(page_url: str, site_key: str) -> dict:
    """Solve hCaptcha using CapSolver"""
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
                    json={
                        "clientKey": CAPSOLVER_API_KEY,
                        "taskId": task_id
                    },
                    timeout=30.0
                )
                
                result_data = result_response.json()
                
                if result_data.get("status") == "ready":
                    print(f"✅ CapSolver: hCaptcha solved!")
                    return {
                        "success": True,
                        "solution": result_data.get("solution", {}).get("gRecaptchaResponse")
                    }
                elif result_data.get("status") == "failed":
                    return {"success": False, "error": result_data.get('errorDescription')}
            
            return {"success": False, "error": "Timeout"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}

async def detect_and_solve_captcha(page) -> tuple[bool, str | None]:
    """Detect CAPTCHA type and solve it using CapSolver"""
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
                        import re
                        match = re.search(r'data-sitekey=["\']([^"\']+)["\']', content)
                        if match:
                            turnstile_sitekey = match.group(1)
                            print(f"✅ Found Turnstile sitekey: {turnstile_sitekey}")
                            break
                    except:
                        pass
                    break
        except:
            pass
        
        if not turnstile_sitekey:
            try:
                turnstile_selectors = [
                    "[data-sitekey]",
                    ".cf-turnstile",
                    "#cf-turnstile",
                    "iframe[src*='turnstile']",
                    "iframe[src*='challenges.cloudflare']"
                ]
                
                for selector in turnstile_selectors:
                    element = page.locator(selector).first
                    if await element.count() > 0:
                        print(f"✅ Found Turnstile element: {selector}")
                        try:
                            turnstile_sitekey = await element.get_attribute("data-sitekey")
                            if turnstile_sitekey:
                                print(f"✅ Extracted Turnstile sitekey: {turnstile_sitekey}")
                                break
                        except:
                            content = await page.content()
                            import re
                            match = re.search(r'data-sitekey=["\']([^"\']+)["\']', content)
                            if match:
                                turnstile_sitekey = match.group(1)
                                print(f"✅ Found Turnstile sitekey from source: {turnstile_sitekey}")
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
                            
                            if (typeof turnstile !== 'undefined') {{
                                try {{ turnstile.reset(); }} catch(e) {{}}
                            }}
                            
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
                            if (frame.src.includes('challenges.cloudflare') || frame.src.includes('turnstile')) {
                                return true;
                            }
                        }
                        const elements = document.querySelectorAll('.cf-turnstile, [data-sitekey]');
                        return elements.length > 0;
                    }
                """)
                
                if still_has_turnstile:
                    print("⚠️ Turnstile still present - may need manual intervention")
                    return (False, None)
                else:
                    print("✅ Turnstile passed!")
                    return (True, None)
            else:
                error = result.get("error", "Unknown error")
                print(f"⚠️ Turnstile solve failed: {error}")
                return (False, None)
        
        print("🤖 Checking for reCAPTCHA...")
        checkbox_clicked = await try_click_recaptcha_checkbox(page)
        
        if checkbox_clicked:
            await asyncio.sleep(4)
            
            has_image_challenge = await page.evaluate("""
                () => {
                    const frames = document.querySelectorAll('iframe');
                    for (let frame of frames) {
                        if (frame.src.includes('recaptcha') && frame.src.includes('bframe')) {
                            return true;
                        }
                    }
                    return false;
                }
            """)
            
            if not has_image_challenge:
                print("✅ reCAPTCHA checkbox click was enough - no image challenge!")
                return (True, None)
            else:
                print("⚠️ Image challenge appeared - proceeding with CapSolver...")
        
        recaptcha_sitekey = None
        try:
            frames = page.frames
            for frame in frames:
                if 'recaptcha' in frame.url and 'anchor' in frame.url:
                    import re
                    match = re.search(r'[?&]k=([^&]+)', frame.url)
                    if match:
                        recaptcha_sitekey = match.group(1)
                        print(f"✅ Found reCAPTCHA sitekey from iframe: {recaptcha_sitekey}")
                        break
        except:
            pass
        
        if not recaptcha_sitekey:
            try:
                sitekey_element = page.locator("[data-sitekey]").first
                if await sitekey_element.count() > 0:
                    recaptcha_sitekey = await sitekey_element.get_attribute("data-sitekey")
                    print(f"✅ Found reCAPTCHA sitekey from element: {recaptcha_sitekey}")
            except:
                pass
        
        if not recaptcha_sitekey:
            try:
                content = await page.content()
                import re
                match = re.search(r'data-sitekey=["\']([^"\']+)["\']', content)
                if match:
                    recaptcha_sitekey = match.group(1)
                    print(f"✅ Found reCAPTCHA sitekey from source: {recaptcha_sitekey}")
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
                                el.innerHTML = token;
                                el.value = token;
                                el.style.display = 'block';
                            }});
                            
                            const textarea = document.getElementById('g-recaptcha-response');
                            if (textarea) {{
                                textarea.innerHTML = token;
                                textarea.value = token;
                            }}
                            
                            const elements = document.querySelectorAll('[data-callback]');
                            elements.forEach(el => {{
                                const callback = el.getAttribute('data-callback');
                                if (callback && typeof window[callback] === 'function') {{
                                    try {{ window[callback](token); }} catch(e) {{}}
                                }}
                            }});
                            
                            if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) {{
                                try {{ grecaptcha.enterprise.execute(); }} catch(e) {{}}
                            }}
                            
                            document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {{
                                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }});
                        }}
                    """, solution_token)
                    print("✅ reCAPTCHA solution injected (method 1)")
                except Exception as inject_error:
                    print(f"⚠️ Injection method 1 failed: {inject_error}")
                    try:
                        await page.evaluate(f"""
                            var token = '{solution_token}';
                            var textareas = document.querySelectorAll('textarea[name="g-recaptcha-response"]');
                            for (var i = 0; i < textareas.length; i++) {{
                                textareas[i].value = token;
                                textareas[i].innerHTML = token;
                            }}
                        """)
                        print("✅ reCAPTCHA solution injected (fallback method)")
                    except Exception as e2:
                        print(f"⚠️ All injection methods failed: {e2}")
                
                print("✅ reCAPTCHA solution injected into page!")
                await asyncio.sleep(3)
                
                submitted = False
                try:
                    form_submitted = await page.evaluate("""
                        () => {
                            const textarea = document.querySelector('textarea[name="g-recaptcha-response"]');
                            if (textarea) {
                                const form = textarea.closest('form');
                                if (form) {
                                    form.submit();
                                    return true;
                                }
                            }
                            return false;
                        }
                    """)
                    
                    if form_submitted:
                        print("✅ Auto-submitted form via JS")
                        submitted = True
                    
                except Exception as e:
                    print(f"⚠️ Form auto-submit failed: {e}")
                
                if not submitted:
                    try:
                        submit_selectors = [
                            "button[type='submit']",
                            "input[type='submit']",
                            "button:has-text('Submit')",
                            "button:has-text('Continue')",
                            "button:has-text('Verify')",
                            "button:has-text('Search')",
                            "[type='submit']",
                            "form button"
                        ]
                        
                        for selector in submit_selectors:
                            try:
                                element = page.locator(selector).first
                                if await element.count() > 0:
                                    await element.click(timeout=3000)
                                    print(f"✅ Clicked submit button: {selector}")
                                    submitted = True
                                    break
                            except:
                                continue
                                
                    except Exception as e:
                        print(f"⚠️ Could not click submit: {e}")
                
                print("⏳ Waiting for page to navigate after CAPTCHA submission...")
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    await asyncio.sleep(3)
                except:
                    await asyncio.sleep(5)
                
                still_has_captcha = await page.evaluate("""
                    () => {
                        const frames = document.querySelectorAll('iframe');
                        for (let frame of frames) {
                            if (frame.src.includes('recaptcha') && frame.src.includes('anchor')) {
                                return true;
                            }
                        }
                        return false;
                    }
                """)
                
                if still_has_captcha:
                    print("⚠️ CAPTCHA still present after solving - Google may be blocking automation")
                    return (False, None)
                else:
                    print("✅ CAPTCHA passed successfully!")
                    return (True, None)
            else:
                error = result.get("error", "Unknown error")
                return (False, f"CapSolver failed: {error}")
        
        hcaptcha_sitekey = None
        try:
            hcaptcha_element = page.locator("[data-sitekey]").first
            if await hcaptcha_element.count() > 0:
                parent_html = await hcaptcha_element.evaluate("el => el.outerHTML")
                if "hcaptcha" in parent_html.lower():
                    hcaptcha_sitekey = await hcaptcha_element.get_attribute("data-sitekey")
                    print(f"✅ Found hCaptcha sitekey: {hcaptcha_sitekey}")
        except:
            pass
        
        if hcaptcha_sitekey:
            print(f"🎯 Detected hCaptcha - Solving with CapSolver...")
            result = await solve_hcaptcha(page_url, hcaptcha_sitekey)
            
            if result.get("success"):
                solution_token = result.get("solution")
                
                await page.evaluate(f"""
                    (token) => {{
                        const textarea = document.querySelector('[name="h-captcha-response"]');
                        if (textarea) {{
                            textarea.innerHTML = token;
                            textarea.value = token;
                        }}
                        
                        if (typeof hcaptcha !== 'undefined') {{
                            const callback = document.querySelector('[data-callback]');
                            if (callback) {{
                                const callbackName = callback.getAttribute('data-callback');
                                if (typeof window[callbackName] === 'function') {{
                                    window[callbackName](token);
                                }}
                            }}
                        }}
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
    """Try to click the 'I'm not a robot' checkbox"""
    try:
        recaptcha_frame = None
        frames = page.frames
        
        for frame in frames:
            frame_url = frame.url
            if 'recaptcha' in frame_url and 'anchor' in frame_url:
                recaptcha_frame = frame
                print(f"✅ Found reCAPTCHA anchor iframe")
                break
        
        if not recaptcha_frame:
            print("⚠️ No reCAPTCHA anchor iframe found")
            return False
        
        checkbox_selectors = [
            ".recaptcha-checkbox-border",
            "#recaptcha-anchor",
            ".rc-anchor-center-item",
            "div.recaptcha-checkbox-checkmark",
            ".recaptcha-checkbox"
        ]
        
        for selector in checkbox_selectors:
            try:
                checkbox = recaptcha_frame.locator(selector).first
                if await checkbox.count() > 0:
                    print(f"✅ Found checkbox with selector: {selector}")
                    await asyncio.sleep(0.5)
                    await checkbox.hover()
                    await asyncio.sleep(0.3)
                    await checkbox.click(timeout=3000)
                    print("✅ Clicked reCAPTCHA checkbox!")
                    return True
                    
            except Exception as e:
                print(f"⚠️ Failed with selector {selector}: {e}")
                continue
        
        print("⚠️ Could not find clickable checkbox")
        return False
        
    except Exception as e:
        print(f"⚠️ Checkbox click failed: {e}")
        return False

async def extract_page_content(page):
    """Extract visible text and images from current page"""
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
                    const elements = document.querySelectorAll('script, style, [hidden], [style*="display: none"]');
                    elements.forEach(el => el.remove());
                    
                    const selectors = [
                        'main', 'article', '[role="main"]',
                        '.content', '#content', '.main-content',
                        'body'
                    ];
                    
                    for (let selector of selectors) {
                        const element = document.querySelector(selector);
                        if (element) {
                            return element.innerText.trim();
                        }
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
                () => {
                    const imgs = Array.from(document.querySelectorAll('img'));
                    return imgs
                        .filter(img => img.src && img.width > 100 && img.height > 100)
                        .slice(0, 10)
                        .map(img => ({
                            src: img.src,
                            alt: img.alt || '',
                            width: img.width,
                            height: img.height
                        }));
                }
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
    """Maximum stealth - harder to detect"""
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        
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
        
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };
        
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
    """Route to best starting URL to avoid captchas"""
    prompt_lower = prompt.lower()
    
    if "amazon" in prompt_lower:
        return "https://www.amazon.in"
    elif "flipkart" in prompt_lower:
        return "https://www.flipkart.com"
    elif "youtube" in prompt_lower:
        return "https://www.youtube.com"
    elif "myntra" in prompt_lower:
        return "https://www.myntra.com"
    elif "swiggy" in prompt_lower:
        return "https://www.swiggy.com"
    elif "zomato" in prompt_lower:
        return "https://www.zomato.com"
    
    return "https://search.brave.com"

async def smart_brave_search(page, query: str):
    """Direct Brave search - privacy-focused, NO captchas"""
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
    """Smart captcha detection + CapSolver auto-solve - never stops task"""
    
    solved, error = await detect_and_solve_captcha(page)
    
    if solved:
        print("🎉 CAPTCHA solved by CapSolver! Continuing task...")
        await asyncio.sleep(2)
        return (False, None)
    
    captcha_indicators = [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']", 
        "iframe[src*='turnstile']",
        "iframe[src*='challenges.cloudflare']",
        ".g-recaptcha",
        "div[class*='rc-anchor']",
        "div.h-captcha",
        ".cf-turnstile"
    ]
    
    for selector in captcha_indicators:
        try:
            element = page.locator(selector).first
            if await element.count() > 0:
                is_visible = await element.is_visible()
                if is_visible:
                    print(f"🚨 CAPTCHA still visible (DOM): {selector}")
                    solved, error = await detect_and_solve_captcha(page)
                    if solved:
                        return (False, None)
                    print("⚠️ Continuing despite CAPTCHA...")
                    return (False, None)
        except:
            continue
    
    return (False, None)

async def attempt_popup_bypass(page):
    """Try common methods to close popups/overlays"""
    try:
        close_selectors = [
            "button:has-text('Close')",
            "button:has-text('No thanks')",
            "button:has-text('Maybe later')",
            "button:has-text('Skip')",
            "button:has-text('Not now')",
            "button:has-text('Accept')",
            "button:has-text('Got it')",
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
        
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)
        
        return False
        
    except Exception as e:
        print(f"⚠️ Popup bypass failed: {e}")
        return False

async def detect_blocking_elements(page, b64_image, client):
    """Use GPT-4o to detect blocking elements"""
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
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}]
                }
            ],
            max_tokens=150,
            response_format={"type": "json_object"}
        )
        
        content = response.choices[0].message.content
        
        if content is None or content.strip() == "":
            return {"blocked": False, "blocker_type": "none", "reason": "detection failed"}
        
        result = json.loads(content)
        return result
        
    except json.JSONDecodeError as e:
        print(f"⚠️ Blocker detection JSON error: {e}")
        return {"blocked": False, "blocker_type": "none", "reason": "JSON parsing failed"}
    except Exception as e:
        print(f"⚠️ Blocker detection failed: {e}")
        return {"blocked": False, "blocker_type": "none", "reason": "detection failed"}

# -------------------------------------------------
# FIX 1: VIDEO CREATION - Use absolute paths to prevent path doubling bug
# -------------------------------------------------
async def create_and_upload_video(folder_path: str, session_id: str) -> str | None:
    """Create video from all screenshots with absolute paths to avoid ffmpeg path doubling"""
    video_path = f"{folder_path}/output.mp4"
    
    try:
        import glob
        import re
        
        screenshots = glob.glob(f"{folder_path}/step_*.png")
        
        def extract_step_number(filename):
            match = re.search(r'step_(\d+)', filename)
            return int(match.group(1)) if match else 999999
        
        screenshots = sorted(screenshots, key=extract_step_number)
        num_screenshots = len(screenshots)
        
        if num_screenshots == 0:
            print("⚠️ No screenshots found for video")
            return None
        
        print(f"📹 Creating video from {num_screenshots} screenshots...")
        print(f"📋 Frame order: {[s.split('/')[-1] for s in screenshots[:5]]}...{[s.split('/')[-1] for s in screenshots[-2:]]}")
        
        file_list_path = f"{folder_path}/file_list.txt"

        # ✅ FIX: Convert all paths to absolute to prevent ffmpeg path doubling
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
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", abs_file_list,
            "-c:v", "libx264", 
            "-pix_fmt", "yuv420p",
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:-1:-1:color=black",
            abs_video_path
        ]
        
        result = subprocess.run(
            command, 
            check=True, 
            capture_output=True, 
            text=True
        )
        
        print(f"✅ Video created successfully: {abs_video_path}")
        
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
                    "content": "You are a debugger. The agent failed to complete the task: '" + prompt + "'. Look at the screenshot carefully. Is there a Login Popup? Is there a Captcha? Is the item out of stock? Explain the BLOCKER in 1 sentence."
                },
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}]}
            ],
            max_tokens=100
        )
        content = response.choices[0].message.content
        return content if content else "Unknown error (could not analyze)."
    except:
        return "Unknown error (could not analyze)."

# -------------------------------------------------
# FIX 2: SMART CLICK WITH PANEL-AWARE TOOL FINDING
# -------------------------------------------------
async def try_find_and_click_tool(page, tool_name: str) -> bool:
    """
    After a tools panel is open, find and click a specific tool by name.
    Tries multiple strategies to locate the tool card/button.
    """
    print(f"🔍 Searching for tool: '{tool_name}'")
    
    # Normalize tool name for partial matching
    tool_lower = tool_name.lower().replace(" ", "")
    tool_words = tool_name.lower().split()

    strategies = [
        # 1. Exact text match on buttons/divs
        lambda: page.locator(f"button, div[role='button'], [class*='tool'], [class*='card']").filter(has_text=tool_name).first,
        # 2. Case-insensitive partial - first word
        lambda: page.locator(f"button, div[role='button'], [class*='tool'], [class*='card']").filter(has_text=tool_words[0]).first if tool_words else None,
        # 3. Any element with the tool name text
        lambda: page.get_by_text(tool_name, exact=False).first,
        # 4. Any element with first word of tool name
        lambda: page.get_by_text(tool_words[0], exact=False).first if tool_words else None,
    ]

    for i, strategy in enumerate(strategies):
        try:
            element = strategy()
            if element and await element.count() > 0 and await element.is_visible():
                print(f"✅ Found tool '{tool_name}' via strategy {i+1}")
                await element.scroll_into_view_if_needed(timeout=3000)
                await asyncio.sleep(0.3)
                await element.click(timeout=5000, force=True)
                print(f"✅ Clicked tool '{tool_name}'!")
                await asyncio.sleep(2)
                return True
        except Exception as e:
            print(f"⚠️ Strategy {i+1} failed: {e}")
            continue

    print(f"❌ Could not find tool '{tool_name}' in panel")
    return False


async def try_click_add_button_smart(page, label: str) -> bool:
    """
    Smart '+' button click:
    - If label is just '+', find the correct '+' button (not Brave Ask, not a text block)
    - Prefer buttons in sidebars/panels/right-side areas
    """
    print(f"🔍 Smart '+' button search...")

    # Strategy 1: aria-label or title containing 'add'
    for selector in [
        "button[aria-label*='add' i]",
        "button[aria-label*='Add' i]",
        "button[title*='add' i]",
        "button[title*='Add tool' i]",
        "[class*='add-tool']",
        "[class*='addTool']",
        "[class*='add_tool']",
        "button[class*='plus']",
        "button[class*='add']:not([class*='addon'])",
    ]:
        try:
            el = page.locator(selector).first
            if await el.count() > 0 and await el.is_visible():
                print(f"✅ Found '+' via selector: {selector}")
                await el.click(timeout=5000, force=True)
                await asyncio.sleep(2)
                return True
        except:
            continue

    # Strategy 2: Find all buttons with text '+' and pick the one most likely in a right panel
    try:
        all_plus = page.locator("button").filter(has_text=_re.compile(r'^\+$'))
        count = await all_plus.count()
        print(f"🔍 Found {count} exact '+' buttons")
        if count > 0:
            # Click the last one (usually the rightmost/newest in layout)
            btn = all_plus.last
            if await btn.is_visible():
                await btn.click(timeout=5000, force=True)
                await asyncio.sleep(2)
                return True
    except Exception as e:
        print(f"⚠️ Exact + button search failed: {e}")

    # Strategy 3: Broad text search for '+'
    try:
        el = page.get_by_text("+", exact=True).first
        if await el.count() > 0 and await el.is_visible():
            await el.click(timeout=5000, force=True)
            await asyncio.sleep(2)
            return True
    except:
        pass

    return False


import re as _re

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
    last_label = None         # ✅ FIX: Track label too, not just action type
    action_repeat_count = 0
    blocker_detected = False
    
    # ✅ FIX: Track panel open state to know when to search for tools vs click '+'
    tools_panel_open = False
    
    extracted_contents = []
    
    extract_content = any(keyword in request.prompt.lower() for keyword in [
        'extract', 'get content', 'scrape', 'fetch content', 'get text',
        'get images', 'capture content', 'save content', 'read content',
        'content from', 'text from', 'images from'
    ])
    
    if extract_content:
        print("📦 Content extraction ENABLED (detected in prompt)")
    else:
        print("⚡ Content extraction DISABLED (not requested)")

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
                                    "Input: 'google best restaurants in tokyo'\n"
                                    "Output: best restaurants in tokyo\n\n"
                                    "Return ONLY the search query, nothing else."
                                )
                            },
                            {"role": "user", "content": request.prompt}
                        ],
                        max_tokens=50
                    )
                    
                    content = extract_response.choices[0].message.content
                    search_query = content.strip() if content else None
                    
                    if not search_query:
                        search_query = None
                    else:
                        print(f"🔍 Extracted search query: '{search_query}'")
                except Exception as e:
                    print(f"⚠️ Query extraction failed: {e}")
                    search_query = None
            
            if search_query:
                if "google" in prompt_lower:
                    print(f"🚫 Google requested but using Brave instead to avoid CAPTCHAs")
                print(f"🎯 Using Brave Search")
                await smart_brave_search(page, search_query)
            else:
                start_url = get_smart_start_url(request.prompt)
                print(f"🎯 Smart routing to: {start_url}")
                await page.goto(start_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)


            # --- THE LOOP (50 steps) ---
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
                    task_involves_login = any(word in request.prompt.lower() for word in ['login', 'sign in', 'log in', 'signin'])
                    
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
                                bypassed = await attempt_popup_bypass(page)
                                await page.wait_for_timeout(1000)
                                continue
                    else:
                        print(f"⏭️ Skipping blocker check - task involves login")

                user_instructions = ""
                if "dont" in request.prompt.lower() or "don't" in request.prompt.lower():
                    dont_matches = _re.findall(r"don'?t[^.!?]*[.!?]", request.prompt, _re.IGNORECASE)
                    if dont_matches:
                        user_instructions = "⚠️ CRITICAL USER INSTRUCTIONS:\n"
                        for instruction in dont_matches:
                            user_instructions += f"- {instruction.strip()}\n"
                        user_instructions += "\n"
                
                if "there is" in request.prompt.lower() or "click" in request.prompt.lower():
                    specific_matches = _re.findall(r"(?:there is|click)[^.!?]*[.!?]", request.prompt, _re.IGNORECASE)
                    if specific_matches and not user_instructions:
                        user_instructions = "⚠️ SPECIFIC INSTRUCTIONS:\n"
                    for instruction in specific_matches:
                        user_instructions += f"- {instruction.strip()}\n"
                
                # ✅ FIX: Inject panel awareness into system prompt
                panel_context = ""
                if tools_panel_open:
                    panel_context = (
                        "🔧 TOOLS PANEL IS CURRENTLY OPEN.\n"
                        "You should now search for and click the specific tool (e.g. 'xai video') in the panel.\n"
                        "Do NOT click '+' again - the panel is already open.\n"
                        "Look for tool cards/items in the panel and click the one matching the task.\n\n"
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
                    "- If tools panel is open → click the specific tool name (e.g. 'xai video'), NOT '+'\n"
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
                    "- After panel opens, look for the tool NAME and click it directly\n"
                    "- If you see a list of tools in a panel, click the tool name (e.g. 'xai video')\n"
                    "- Do NOT keep clicking the same element repeatedly\n\n"
                    "🚫 ANTI-DISTRACTION RULES:\n"
                    "- Ignore flashcards and promotional suggestions unless explicitly asked\n"
                    "- Focus ONLY on elements needed to complete the task\n\n"
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
                    
                    if content is None or content.strip() == "":
                        print(f"⚠️ GPT-4o returned empty response, retrying...")
                        await asyncio.sleep(2)
                        continue
                    
                    decision = json.loads(content)
                    
                    current_state = decision.get('current_state', 'Unknown')
                    completed = decision.get('completed_steps', 'Unknown')
                    remaining = decision.get('remaining_steps', 'Unknown')
                    
                    print(f"🔍 STATE ANALYSIS:")
                    print(f"   Current: {current_state}")
                    print(f"   Done: {completed}")
                    print(f"   ToDo: {remaining}")
                    
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
                
                # ✅ FIX: Track action+label combo to detect true loops (not just same action type)
                action_signature = f"{current_action}:{current_label}"
                if action_signature == f"{last_action}:{last_label}":
                    action_repeat_count += 1
                else:
                    action_repeat_count = 0
                    last_action = current_action
                    last_label = current_label
                
                # ✅ FIX: Raise repeat threshold to 7 (was 5) and add smarter recovery
                if action_repeat_count >= 7:
                    print(f"🛑 Same action '{action_signature}' repeated {action_repeat_count} times. Giving up.")
                    final_message = f"Failed: Stuck in loop - action '{current_action}:{current_label}' repeated {action_repeat_count} times"
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
                        
                        verification_result = final_verification.choices[0].message.content
                        
                        if not verification_result:
                            verification_result = "COMPLETE"
                        
                        print(f"🔍 Verification result: {verification_result}")
                        
                        if "COMPLETE" in verification_result.upper():
                            print("✅ Verification PASSED - Task is actually complete!")
                        elif "BLOCKED" in verification_result.upper():
                            print("🚫 Verification found blocker - Task failed")
                            final_message = f"Failed: {verification_result}"
                            final_status = "failed"
                            blocker_detected = True
                            break
                        else:
                            print(f"❌ Verification FAILED - Task not complete: {verification_result}")
                            print("🔄 Continuing task from current state...")
                            consecutive_failures += 1
                            
                            if action_repeat_count >= 3:
                                print("🛑 Agent keeps saying 'done' but task incomplete. Giving up.")
                                final_message = f"Failed: Agent completed task incorrectly - {verification_result}"
                                final_status = "failed"
                                break
                            
                            continue
                            
                    except Exception as e:
                        print(f"⚠️ Final verification failed: {e}")
                    
                    if step < 4:
                        print(f"⚠️ Agent tried to finish at step {step} (too early). Verifying...")
                        verify_response = await client.chat.completions.create(
                            model="gpt-4o-mini",
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
                        
                        if not verification:
                            verification = "YES"
                        
                        print(f"🔍 Verification: {verification}")
                        
                        if "NO" in verification.upper():
                            print(f"❌ Verification failed. Continuing task...")
                            consecutive_failures += 1
                            continue
                    
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
                    
                    print(f"✅ Final screenshot saved: step_{step + 1}_FINAL.png")
                    
                    final_message = f"Success: {reason}"
                    final_status = "success"
                    break
                
                action_succeeded = False
                
                try:
                    if decision['action'] == 'click':
                        label = decision.get('label', '')
                        print(f"🎯 Attempting to click: '{label}'")
                        
                        # ✅ FIX: If panel is open, try to find the tool directly by name
                        if tools_panel_open and label.strip() != '+':
                            tool_clicked = await try_find_and_click_tool(page, label)
                            if tool_clicked:
                                action_succeeded = True
                                tools_panel_open = False  # Tool added, panel likely closed
                                # Reset repeat counter since this was a new successful action
                                action_repeat_count = 0
                            else:
                                print(f"⚠️ Tool search failed, falling through to normal click...")

                        if not action_succeeded:
                            element = None
                            found_method = None
                            
                            # ✅ FIX: Special case for '+' button - use smart click
                            if label.strip() == '+':
                                plus_clicked = await try_click_add_button_smart(page, label)
                                if plus_clicked:
                                    action_succeeded = True
                                    tools_panel_open = True  # Mark panel as open after '+' click
                                    action_repeat_count = 0  # Reset since panel state changed
                                    print("✅ '+' button clicked, tools panel should now be open")
                            
                            if not action_succeeded:
                                # Special case: Add to cart on Amazon
                                if label.lower() == "add to cart":
                                    try:
                                        element = page.locator("#add-to-cart-button, input[name='submit.add-to-cart'], button:has-text('Add to Cart'):visible").first
                                        if await element.count() > 0 and await element.is_visible():
                                            found_method = "Main Add to cart button"
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
                                        label_words = label.split()[:5]
                                        partial_label = ' '.join(label_words)
                                        element = page.get_by_role("link").filter(has_text=partial_label).first
                                        if await element.count() > 0:
                                            found_method = "Partial link match"
                                    except:
                                        pass
                                
                                if not found_method:
                                    try:
                                        label_words = label.split()[:3]
                                        shorter_label = ' '.join(label_words)
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

                                if found_method and await element.count() > 0:
                                    print(f"✅ Found element using: {found_method}")
                                    
                                    click_attempts = 0
                                    max_click_attempts = 3
                                    
                                    while click_attempts < max_click_attempts and not action_succeeded:
                                        click_attempts += 1
                                        if click_attempts > 1:
                                            print(f"🔄 Retry attempt {click_attempts}/{max_click_attempts}")
                                        
                                        try:
                                            await element.scroll_into_view_if_needed(timeout=3000)
                                            await asyncio.sleep(0.3)
                                        except Exception as scroll_err:
                                            print(f"⚠️ Scroll failed: {scroll_err}")
                                        
                                        try:
                                            await element.hover(timeout=3000)
                                            await asyncio.sleep(0.3)
                                        except Exception as hover_err:
                                            print(f"⚠️ Hover failed (clicking anyway): {hover_err}")
                                        
                                        try:
                                            await element.click(timeout=5000, force=True)
                                            await asyncio.sleep(2)
                                            print(f"✅ Click executed (attempt {click_attempts})")
                                            
                                            try:
                                                still_there = await element.count() > 0
                                                if still_there and click_attempts < max_click_attempts:
                                                    is_visible = await element.is_visible()
                                                    if is_visible:
                                                        print(f"🔍 Element still visible - might need retry")
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
                                            if click_attempts < max_click_attempts:
                                                await asyncio.sleep(1)
                                                continue
                                            else:
                                                action_succeeded = False
                                    
                                    if action_succeeded:
                                        print(f"✅ Click completed successfully after {click_attempts} attempt(s)!")
                                        await asyncio.sleep(2)
                                        
                                        try:
                                            dismiss_selectors = [
                                                "button[aria-label*='Close']",
                                                "button[aria-label*='Dismiss']",
                                                "button:has-text('×')",
                                                "button:has-text('Close')",
                                                "button:has-text('Got it')",
                                                "button:has-text('OK')",
                                                "button.close",
                                                "[data-dismiss]",
                                                ".notification-close",
                                                ".toast-close"
                                            ]
                                            
                                            dismissed_something = False
                                            for dismiss_sel in dismiss_selectors:
                                                try:
                                                    dismiss_btn = page.locator(dismiss_sel).first
                                                    if await dismiss_btn.count() > 0 and await dismiss_btn.is_visible():
                                                        await dismiss_btn.click(timeout=2000)
                                                        dismissed_something = True
                                                        await asyncio.sleep(0.5)
                                                        break
                                                except:
                                                    continue
                                            
                                            if not dismissed_something:
                                                await page.keyboard.press("Escape")
                                                await asyncio.sleep(0.5)
                                            
                                        except:
                                            pass
                                        
                                        await asyncio.sleep(1)
                                    
                                    if "amazon" in page.url.lower():
                                        try:
                                            await asyncio.sleep(2)
                                            amazon_dismiss_selectors = [
                                                "button[data-action='a-popover-close']",
                                                "a.a-popover-close",
                                                "button.a-button-close",
                                                "button:has-text('Not now')",
                                                "button:has-text('Skip')",
                                                "button:has-text('Maybe later')",
                                                "a:has-text('Skip sign in')",
                                                "[aria-label='Close']"
                                            ]
                                            
                                            dismissed = False
                                            for sel in amazon_dismiss_selectors:
                                                try:
                                                    dismiss_btn = page.locator(sel).first
                                                    if await dismiss_btn.count() > 0 and await dismiss_btn.is_visible():
                                                        await dismiss_btn.click(timeout=2000, force=True)
                                                        dismissed = True
                                                        await asyncio.sleep(1)
                                                        break
                                                except:
                                                    continue
                                            
                                            if not dismissed:
                                                for i in range(3):
                                                    await page.keyboard.press("Escape")
                                                    await asyncio.sleep(0.3)
                                        except:
                                            pass
                                    
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
                        
                        is_password = any(word in decision.get('reason', '').lower() for word in ['password', 'pass']) or \
                                     'password' in text_to_type.lower() or \
                                     (len(text_to_type) >= 6 and any(c in text_to_type for c in ['@', '!', '#', '$', '%']))
                        
                        is_email = '@' in text_to_type and '.' in text_to_type
                        
                        print(f"📝 Typing: {text_to_type[:20]}... (password={is_password}, email={is_email})")
                        
                        input_element = None
                        input_found = False
                        
                        if is_password:
                            try:
                                input_element = page.locator("input[type='password']:visible, input[name='password']:visible, input[placeholder*='password' i]:visible").first
                                if await input_element.count() > 0 and await input_element.is_visible():
                                    input_found = True
                            except:
                                pass
                        
                        if not input_found and is_email:
                            try:
                                input_element = page.locator("input[type='email']:visible, input[name='email']:visible, input[name='username']:visible, input[placeholder*='email' i]:visible").first
                                if await input_element.count() > 0 and await input_element.is_visible():
                                    input_found = True
                            except:
                                pass
                        
                        if not input_found and not is_password and not is_email:
                            try:
                                input_element = page.locator("input[type='search']:visible, [aria-label='Search']:visible").first
                                if await input_element.count() > 0 and await input_element.is_visible():
                                    input_found = True
                            except:
                                pass
                        
                        if not input_found:
                            try:
                                input_element = page.locator("input:visible:not([type='hidden']):not([type='submit']):not([type='button'])").first
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
                                input_element = page.locator("input:visible, textarea:visible").first
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
                                try:
                                    continue_selectors = [
                                        "button:has-text('Continue')",
                                        "button:has-text('Next')",
                                        "button:has-text('Submit')",
                                        "button[type='submit']",
                                        "input[type='submit']"
                                    ]
                                    for cont_sel in continue_selectors:
                                        cont_btn = page.locator(cont_sel).first
                                        if await cont_btn.count() > 0 and await cont_btn.is_visible():
                                            has_continue_button = True
                                            print(f"ℹ️ Continue/Submit button detected - next action should be CLICK")
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
                content_summary = {
                    "total_pages": len(extracted_contents),
                    "pages": extracted_contents
                }

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
            content_summary = {
                "total_pages": len(extracted_contents),
                "pages": extracted_contents
            }
        
        return {
            "status": "error",
            "result": f"System Error: {str(e)}",
            "video_url": video_url,
            "extracted_content": content_summary
        }

# Run with: python -m uvicorn main:app --reload
