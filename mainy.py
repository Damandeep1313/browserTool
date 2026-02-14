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

# -------------------------------------------------
# 4. CAPSOLVER INTEGRATION
# -------------------------------------------------

async def solve_cloudflare_turnstile(page_url: str, site_key: str) -> dict:
    """Solve Cloudflare Turnstile using CapSolver"""
    try:
        print(f"🔧 CapSolver: Solving Cloudflare Turnstile...")
        
        async with httpx.AsyncClient() as client:
            # Create task
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
            
            # Poll for result (max 120 seconds)
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
            # Create task
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
            
            # Poll for result (max 120 seconds)
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
        
        # Check for Cloudflare Turnstile FIRST (most common now)
        turnstile_sitekey = None
        try:
            # Method 1: Check for Turnstile iframe
            frames = page.frames
            for frame in frames:
                if 'challenges.cloudflare.com' in frame.url or 'turnstile' in frame.url.lower():
                    print("✅ Detected Cloudflare Turnstile iframe")
                    # Try to extract sitekey from parent page
                    try:
                        content = await page.content()
                        import re
                        # Look for Turnstile sitekey pattern
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
        
        # Method 2: Check for Turnstile elements in DOM
        if not turnstile_sitekey:
            try:
                # Look for common Turnstile selectors
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
                            # Try to get from page source
                            content = await page.content()
                            import re
                            match = re.search(r'data-sitekey=["\']([^"\']+)["\']', content)
                            if match:
                                turnstile_sitekey = match.group(1)
                                print(f"✅ Found Turnstile sitekey from source: {turnstile_sitekey}")
                                break
            except:
                pass
        
        # If Turnstile detected, solve it
        if turnstile_sitekey:
            print(f"🎯 Detected Cloudflare Turnstile - Solving with CapSolver...")
            result = await solve_cloudflare_turnstile(page_url, turnstile_sitekey)
            
            if result.get("success"):
                solution_token = result.get("solution")
                
                # Inject Turnstile solution
                try:
                    await page.evaluate(f"""
                        (token) => {{
                            // Method 1: Find and set Turnstile response input
                            const inputs = document.querySelectorAll('input[name*="cf-turnstile-response"], input[name*="turnstile"]');
                            inputs.forEach(input => {{
                                input.value = token;
                                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }});
                            
                            // Method 2: Try to set via Turnstile API if available
                            if (typeof turnstile !== 'undefined') {{
                                try {{
                                    turnstile.reset();
                                }} catch(e) {{}}
                            }}
                            
                            // Method 3: Trigger any callbacks
                            const turnstileElement = document.querySelector('.cf-turnstile, [data-sitekey]');
                            if (turnstileElement) {{
                                const callback = turnstileElement.getAttribute('data-callback');
                                if (callback && typeof window[callback] === 'function') {{
                                    try {{
                                        window[callback](token);
                                    }} catch(e) {{}}
                                }}
                            }}
                        }}
                    """, solution_token)
                    print("✅ Turnstile solution injected!")
                except Exception as e:
                    print(f"⚠️ Turnstile injection failed: {e}")
                
                await asyncio.sleep(3)
                
                # Check if Turnstile is still present
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
                    return (False, None)  # Don't fail, let it continue
                else:
                    print("✅ Turnstile passed!")
                    return (True, None)
            else:
                error = result.get("error", "Unknown error")
                print(f"⚠️ Turnstile solve failed: {error}")
                return (False, None)  # Don't fail, continue
        
        # THEN: Try to click the "I'm not a robot" checkbox for reCAPTCHA
        print("🤖 Checking for reCAPTCHA...")
        checkbox_clicked = await try_click_recaptcha_checkbox(page)
        
        if checkbox_clicked:
            # Wait to see if it passes without image challenge
            await asyncio.sleep(4)
            
            # Check if image challenge appeared
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
        
        # If checkbox click didn't work or image challenge appeared, use CapSolver
        # Check for reCAPTCHA v2
        recaptcha_sitekey = None
        try:
            # Method 1: Check iframe src
            frames = page.frames
            for frame in frames:
                if 'recaptcha' in frame.url and 'anchor' in frame.url:
                    # Extract sitekey from URL
                    import re
                    match = re.search(r'[?&]k=([^&]+)', frame.url)
                    if match:
                        recaptcha_sitekey = match.group(1)
                        print(f"✅ Found reCAPTCHA sitekey from iframe: {recaptcha_sitekey}")
                        break
        except:
            pass
        
        # Method 2: Check for data-sitekey attribute
        if not recaptcha_sitekey:
            try:
                sitekey_element = page.locator("[data-sitekey]").first
                if await sitekey_element.count() > 0:
                    recaptcha_sitekey = await sitekey_element.get_attribute("data-sitekey")
                    print(f"✅ Found reCAPTCHA sitekey from element: {recaptcha_sitekey}")
            except:
                pass
        
        # Method 3: Check page source
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
        
        # If reCAPTCHA found, solve it
        if recaptcha_sitekey:
            print(f"🎯 Detected reCAPTCHA v2 - Solving with CapSolver...")
            result = await solve_recaptcha_v2(page_url, recaptcha_sitekey)
            
            if result.get("success"):
                solution_token = result.get("solution")
                
                # Inject the solution into the page - ROBUST METHOD
                try:
                    await page.evaluate(f"""
                        (token) => {{
                            // Method 1: Set all g-recaptcha-response textareas
                            document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {{
                                el.innerHTML = token;
                                el.value = token;
                                el.style.display = 'block';
                            }});
                            
                            // Method 2: Set by ID
                            const textarea = document.getElementById('g-recaptcha-response');
                            if (textarea) {{
                                textarea.innerHTML = token;
                                textarea.value = token;
                            }}
                            
                            // Method 3: Trigger callbacks
                            const elements = document.querySelectorAll('[data-callback]');
                            elements.forEach(el => {{
                                const callback = el.getAttribute('data-callback');
                                if (callback && typeof window[callback] === 'function') {{
                                    try {{
                                        window[callback](token);
                                    }} catch(e) {{}}
                                }}
                            }});
                            
                            // Method 4: Try to execute grecaptcha enterprise callback
                            if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) {{
                                try {{
                                    grecaptcha.enterprise.execute();
                                }} catch(e) {{}}
                            }}
                            
                            // Method 5: Dispatch events
                            document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {{
                                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }});
                        }}
                    """, solution_token)
                    print("✅ reCAPTCHA solution injected (method 1)")
                except Exception as inject_error:
                    print(f"⚠️ Injection method 1 failed: {inject_error}")
                    
                    # FALLBACK: Direct textarea manipulation
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
                await asyncio.sleep(3)  # Wait for page to process
                
                # Try to auto-submit the form
                submitted = False
                try:
                    # Method 1: Find and submit the form containing the CAPTCHA
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
                
                # Method 2: Try clicking submit buttons
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
                
                # Wait for navigation/page change
                print("⏳ Waiting for page to navigate after CAPTCHA submission...")
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    await asyncio.sleep(3)
                except:
                    await asyncio.sleep(5)
                
                # Verify we actually passed the CAPTCHA
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
                    # Don't return error - let the agent continue and try again
                    return (False, None)
                else:
                    print("✅ CAPTCHA passed successfully!")
                    return (True, None)
            else:
                error = result.get("error", "Unknown error")
                return (False, f"CapSolver failed: {error}")
        
        # Check for hCaptcha
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
        
        # No CAPTCHA detected
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
        # Find the reCAPTCHA iframe
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
        
        # Try different checkbox selectors
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
                    
                    # Human-like behavior
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

# Helper: Get Screenshot as Base64
async def get_b64_screenshot(page):
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=3000)
    except:
        pass
    
    screenshot_bytes = await page.screenshot()
    return base64.b64encode(screenshot_bytes).decode("utf-8")

# Helper: ULTIMATE Stealth Injection
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

# Helper: Smart URL Router (Use Brave Search - NO CAPTCHAS!)
def get_smart_start_url(prompt: str):
    """Route to best starting URL to avoid captchas"""
    prompt_lower = prompt.lower()
    
    # Direct site routing
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
    
    # Default: Brave Search (ZERO captchas, privacy-focused)
    return "https://search.brave.com"

# Helper: Smart Brave Search (NO CAPTCHAS EVER!)
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

# Helper: Check for CAPTCHA and solve with CapSolver - SIMPLIFIED!
async def check_and_handle_captcha(page, client, last_b64_image):
    """Smart captcha detection + CapSolver auto-solve - never stops task"""
    
    # Try CapSolver detection and solving (now includes Turnstile!)
    solved, error = await detect_and_solve_captcha(page)
    
    if solved:
        print("🎉 CAPTCHA solved by CapSolver! Continuing task...")
        await asyncio.sleep(2)
        return (False, None)
    
    # Check if there's a visible CAPTCHA element
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
                    # Retry once more
                    solved, error = await detect_and_solve_captcha(page)
                    if solved:
                        return (False, None)
                    # If still can't solve, just continue anyway
                    print("⚠️ Continuing despite CAPTCHA...")
                    return (False, None)
        except:
            continue
    
    # No CAPTCHA detected or all solved
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
        
        content = response.choices[0].message.content
        
        if content is None or content.strip() == "":
            print(f"⚠️ Blocker detection: GPT-4o returned empty response")
            return {"blocked": False, "blocker_type": "none", "reason": "detection failed"}
        
        result = json.loads(content)
        return result
        
    except json.JSONDecodeError as e:
        print(f"⚠️ Blocker detection JSON error: {e}")
        return {"blocked": False, "blocker_type": "none", "reason": "JSON parsing failed"}
    except Exception as e:
        print(f"⚠️ Blocker detection failed: {e}")
        return {"blocked": False, "blocker_type": "none", "reason": "detection failed"}

# Helper: Video Creation & Upload & Cleanup
async def create_and_upload_video(folder_path: str, session_id: str) -> str | None:
    """Create video from all screenshots with better frame rate"""
    video_path = f"{folder_path}/output.mp4"
    
    try:
        # Count how many screenshots we have
        import glob
        screenshots = sorted(glob.glob(f"{folder_path}/step_*.png"))
        num_screenshots = len(screenshots)
        
        if num_screenshots == 0:
            print("⚠️ No screenshots found for video")
            return None
        
        print(f"📹 Creating video from {num_screenshots} screenshots...")
        
        # Use 0.5 fps (2 seconds per frame) for better viewing
        command = [
            "ffmpeg", "-y", 
            "-framerate", "0.5",  # 2 seconds per screenshot
            "-pattern_type", "glob",
            "-i", f"{folder_path}/step_*.png",
            "-c:v", "libx264", 
            "-pix_fmt", "yuv420p",
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:-1:-1:color=black",
            video_path
        ]
        
        result = subprocess.run(
            command, 
            check=True, 
            capture_output=True, 
            text=True
        )
        
        print(f"✅ Video created successfully: {video_path}")
        
        # Upload to Cloudinary
        upload_result = cloudinary.uploader.upload(
            video_path, 
            resource_type="video",
            public_id=f"agent_runs/{session_id}", 
            overwrite=True,
            chunk_size=6000000  # 6MB chunks for large videos
        )
        
        video_url = upload_result.get("secure_url")
        print(f"✅ Video uploaded: {video_url}")
        
        # Clean up local files
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
        content = response.choices[0].message.content
        return content if content else "Unknown error (could not analyze)."
    except:
        return "Unknown error (could not analyze)."

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
    
    # Track repeated failures to avoid endless loops
    consecutive_failures = 0
    last_action = None
    action_repeat_count = 0
    blocker_detected = False

    try:
        async with async_playwright() as p:
            
            # ---------------------------------------------------------
            # BROWSER LAUNCH - MAXIMUM STEALTH MODE
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
                    "--disable-gpu",
                    "--window-size=1920,1080",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding"
                ]
            ) 
            
            # Context with ULTRA realistic fingerprinting
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
            # ---------------------------------------------------------
            
            # Start with one page
            page = await context.new_page()
            
            # Apply stealth
            await apply_ultimate_stealth(page)
            
            print(f"🚀 Starting Task: {request.prompt}")
            
            # SMART NAVIGATION - Use GPT to extract search query
            prompt_lower = request.prompt.lower()
            search_query = None
            
            # Check if this involves searching
            if any(word in prompt_lower for word in ["search", "google", "find", "look for"]):
                # Use GPT-4o to extract the actual search query
                try:
                    extract_response = await client.chat.completions.create(
                        model="gpt-4o",
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
                            {
                                "role": "user",
                                "content": request.prompt
                            }
                        ],
                        max_tokens=50
                    )
                    
                    content = extract_response.choices[0].message.content
                    search_query = content.strip() if content else None
                    
                    if not search_query:
                        print(f"⚠️ Query extraction returned empty")
                        search_query = None
                    else:
                        print(f"🔍 Extracted search query: '{search_query}'")
                except Exception as e:
                    print(f"⚠️ Query extraction failed: {e}")
                    search_query = None
            
            # Route based on intent
            if search_query:
                # For search tasks, ALWAYS use Brave (ZERO captchas!)
                if "google" in prompt_lower:
                    print(f"🚫 Google requested but using Brave instead to avoid CAPTCHAs")
                print(f"🎯 Using Brave Search")
                await smart_brave_search(page, search_query)
            else:
                # Use smart routing for non-search tasks
                start_url = get_smart_start_url(request.prompt)
                print(f"🎯 Smart routing to: {start_url}")
                await page.goto(start_url, wait_until="domcontentloaded")
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

                # IMMEDIATE CAPTCHA CHECK WITH CAPSOLVER (never stops task)
                should_stop, captcha_reason = await check_and_handle_captcha(page, client, last_b64_image)
                if should_stop:
                    print(f"🛑 Captcha could not be solved: {captcha_reason}")
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

                # Enhanced prompt
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
                    "6. NEVER click on 'Google' or 'Switch to Google' links. Stay on the current search engine (Brave).\n"
                    "7. If you see a CAPTCHA, just describe what you see and continue - don't try to solve it manually.\n"
                    "8. If you see a login popup that won't close, return action='done' with reason='Blocked by login requirement'.\n"
                    "9. For search results, click on the FIRST relevant article/link to navigate to the actual page.\n\n"
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
                try:
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

                    content = response.choices[0].message.content
                    
                    if content is None or content.strip() == "":
                        print(f"⚠️ GPT-4o returned empty response, retrying...")
                        await asyncio.sleep(2)
                        continue
                    
                    decision = json.loads(content)
                    
                except json.JSONDecodeError as e:
                    print(f"⚠️ JSON decode error: {e}")
                    print(f"Raw response: {content}")
                    # Default to continuing without action
                    decision = {"action": "done", "reason": "JSON parsing failed"}
                except Exception as e:
                    print(f"⚠️ GPT-4o error: {e}")
                    await asyncio.sleep(2)
                    continue
                
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
                    if any(word in reason_lower for word in ["blocked", "robot", "login"]) and "captcha" not in reason_lower:
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
                        
                        if not verification:
                            print(f"⚠️ Verification returned empty, assuming YES")
                            verification = "YES"
                        
                        print(f"🔍 Verification: {verification}")
                        
                        if "NO" in verification.upper():
                            print(f"❌ Verification failed. Continuing task...")
                            consecutive_failures += 1
                            continue
                    
                    # 🆕 CAPTURE FINAL STATE BEFORE CLOSING
                    print("📸 Task complete! Capturing final screenshot...")
                    await asyncio.sleep(3)  # Wait for any final animations/page updates
                    
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=5000)
                    except:
                        pass
                    
                    # Take final screenshot
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
                        
                        # Smart Click Hierarchy
                        element = page.get_by_role("link", name=label).first
                        if await element.count() == 0:
                            element = page.get_by_role("button", name=label).first
                        if await element.count() == 0:
                            element = page.locator("input, textarea, button").filter(has_text=label).first
                        if await element.count() == 0:
                            element = page.get_by_text(label, exact=False).first

                        if await element.count() > 0:
                            # Human-like interaction
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
