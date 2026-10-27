#  SPDX-License-Identifier: Apache-2.0
#  Vendored from authcaptureproxy 1.3.7 (https://github.com/alandtse/auth_capture_proxy),
#  Copyright 2021 Alan Tse. Modified for Parcel Tracking NL: relative imports,
#  no logging of form data, cookies, headers or query strings, lazy imports.
# ruff: noqa
"""
Python Package for auth capture proxy.

Example interceptor for Amazon WAF CAPTCHA authentication flow.

This interceptor handles:
- Multi-host AJAX routing for Amazon subdomains (awswaf.com, amazoncognito.com, etc.)
- CVF (Customer Verification Flow) POST data modification with aamation token validation
- P shim and jQuery shim injection for Amazon's A-framework in AJAX responses
- Submit blocker and AJAX proxy injection for CVF full-page navigations
"""

import base64
import json as _json
import logging
import re

from yarl import URL

from .interceptor import BaseInterceptor, InterceptContext

_LOGGER = logging.getLogger(__name__)


class AmazonWAFInterceptor(BaseInterceptor):
    """Interceptor for Amazon WAF CAPTCHA and CVF authentication flow."""

    async def on_request(self, ctx: InterceptContext) -> None:
        """Handle multi-host AJAX routing for Amazon subdomains.

        Requests with the ``/__amzn_host__`` path marker are routed to the
        specified Amazon subdomain instead of the default host.
        """
        _amzn_host_marker = "/__amzn_host__"
        _req_path = URL(str(ctx.request.url)).path
        if _amzn_host_marker not in _req_path:
            return

        _marker_pos = _req_path.index(_amzn_host_marker) + len(_amzn_host_marker)
        _remaining = _req_path[_marker_pos:]
        _slash_pos = _remaining.find("/")
        if _slash_pos > 0:
            _alt_host = _remaining[:_slash_pos]
            _alt_path = _remaining[_slash_pos:]
        else:
            _alt_host = _remaining
            _alt_path = "/"
        if not _alt_host:
            _LOGGER.warning(
                "Malformed __amzn_host__ path: no host in %s",
                _req_path,
            )
            ctx.short_circuit = await ctx.proxy._build_response(
                text="Invalid multi-host AJAX path",
            )
            return
        # Derive allowed hosts from the proxy target domain
        _root_host = re.sub(r"^www\.", "", str(ctx.host_url.host or ""))
        _allowed_host_pattern = (
            rf"(^|\.)({re.escape(_root_host)}"
            r"|awswaf\.com|amazoncognito\.com|ssl-images-amazon\.com)$"
        )
        if not re.search(_allowed_host_pattern, _alt_host):
            _LOGGER.warning(
                "Blocked request to non-Amazon host via __amzn_host__: %s",
                _alt_host,
            )
            ctx.short_circuit = await ctx.proxy._build_response(
                text="Host not allowed",
            )
            return
        site = f"https://{_alt_host}{_alt_path}"
        if ctx.request.query_string:
            site += f"?{ctx.request.query_string}"
        _LOGGER.debug(
            "Multi-host AJAX proxy: %s -> %s",
            _req_path,
            URL(site).with_query(None),
        )
        ctx.site = site

    async def on_request_data(self, ctx: InterceptContext) -> None:
        """Handle CVF POST data modification.

        For signin POSTs: logs password presence (TOTP no longer appended).
        For CVF verify POSTs: validates aamation token, clears failed challenge
        data, and injects TOTP when available.
        """
        if not ctx.data:
            return
        data = ctx.data
        _login = getattr(ctx.proxy, "_login", None)

        # Signin POST logging
        if data.get("password") and _login is not None and "/ap/signin" in ctx.site:
            _LOGGER.debug(
                "Signin POST: password present (not appending TOTP), site: %s",
                ctx.site,
            )

        # CVF verify POST: validate aamation token, inject OTP
        if _login is not None and "/ap/cvf/verify" in ctx.site:
            _aam_token = data.get("cvf_aamation_response_token", "")
            _LOGGER.debug(
                "CVF verify POST: aamation_token='%.40s', "
                "error_code='%s', captcha_action='%s', "
                "clientSideContext=%s",
                _aam_token,
                data.get("cvf_aamation_error_code", ""),
                data.get("cvf_captcha_captcha_action", ""),
                "present" if data.get("clientSideContext") else "absent",
            )
            _aam_token_valid = False
            if _aam_token:
                try:
                    _padded = _aam_token + "=" * (-len(_aam_token) % 4)
                    _decoded_token = base64.urlsafe_b64decode(_padded)
                    _json.loads(_decoded_token)
                    _aam_token_valid = True
                except Exception:  # nosec B110
                    pass
            if _aam_token_valid:
                _LOGGER.debug(
                    "CVF verify: valid aamation token detected, "
                    "forwarding as-is with %d fields",
                    len(data),
                )
            else:
                # Aamation challenge failed - clear and inject OTP
                data["cvf_aamation_response_token"] = ""  # nosec B105
                data["cvf_aamation_error_code"] = ""  # nosec B105
                data["cvf_captcha_captcha_action"] = ""  # nosec B105
                _get_totp = getattr(_login, "get_totp_token", None)
                _totp_for_cvf = _get_totp() if callable(_get_totp) else None
                if _totp_for_cvf:
                    data["otpCode"] = _totp_for_cvf
                    data["rememberDevice"] = "true"
                _LOGGER.debug(
                    "CVF verify: no valid aamation, cleared fields, OTP=%s",
                    "injected" if _totp_for_cvf else "not available",
                )

    async def on_response(self, ctx: InterceptContext) -> None:
        """Log CVF page detection for debugging."""
        resp = ctx.response
        if (
            resp is not None
            and resp.status_code == 200
            and getattr(ctx.proxy, "_login", None) is not None
            and "/ap/cvf/" in URL(str(resp.url)).path
        ):
            _LOGGER.debug(
                "CVF page detected at %s - browser aamation challenge",
                resp.url,
            )

    async def on_ajax_html(self, ctx: InterceptContext) -> None:
        """Inject P shim + AJAX proxy into aaut/verify/cvf AJAX responses.

        Amazon's A-framework requires a ``P.when('A','ready')`` shim and a
        mini jQuery for CaptchaScript to execute. This hook also rewrites
        awswaf.com script sources to load through the proxy.
        """
        _req_path = URL(str(ctx.request.url)).path
        if "/aaut/verify/cvf" not in _req_path or not ctx.body:
            return

        try:
            _decoded = ctx.body.decode("utf-8", errors="replace")
            # Extract awswaf.com hostname from script src
            _awswaf_match = re.search(
                r'src=["\']https?://([a-z0-9.\-]+\.awswaf\.com)',
                _decoded,
                re.IGNORECASE,
            )
            _awswaf_host = _awswaf_match.group(1) if _awswaf_match else ""
            _LOGGER.debug(
                "Extracted awswaf host from aaut HTML: %s",
                _awswaf_host or "(not found)",
            )
            # Extract Amazon domain for WAF captcha
            _amazon_domain = str(ctx.host_url.host) if ctx.host_url else ""
            _LOGGER.debug(
                "Amazon domain for WAF captcha: %s",
                _amazon_domain or "(not found)",
            )
            # Sanitize hostnames before interpolating into JavaScript
            _safe_host_re = re.compile(r"^[a-z0-9.\-]+$", re.IGNORECASE)
            if _awswaf_host and not _safe_host_re.match(_awswaf_host):
                _LOGGER.warning("Skipping invalid awswaf host: %s", _awswaf_host)
                _awswaf_host = ""
            if _amazon_domain and not _safe_host_re.match(_amazon_domain):
                _LOGGER.warning("Skipping invalid amazon domain: %s", _amazon_domain)
                _amazon_domain = ""
            # AJAX proxy wrapper for aaut iframe context
            _aaut_ajax_proxy = (
                "<script>"
                "(function(){"
                'var pp=window.location.pathname.split("/aaut/")[0];'
                "if(!pp||pp===window.location.pathname)"
                'pp=window.location.pathname.split("/ap/")[0];'
                'var wafHost="' + _awswaf_host + '";'
                'var amazonDomain="' + _amazon_domain + '";'
                "function rw(u){"
                "try{var p=new URL(u,window.location.href);"
                "if(p.hostname.match(/\\.awswaf\\.com$/)){"
                'return pp+"/__amzn_host__"+p.hostname+p.pathname+p.search;}'
                "if(wafHost&&p.hostname===window.location.hostname"
                '&&p.pathname.indexOf("/ait/")===0){'
                'return pp+"/__amzn_host__"+wafHost+p.pathname+p.search;}'
                "if(p.hostname.match(/\\.(amazon\\.(com|it|co\\.uk|de|nl|com\\.be|pl|se|fr|es|co\\.jp|ca"
                "|com\\.au|in|com\\.br)|amazoncognito\\.com)$/)){"
                'if(p.hostname==="www.amazon.com"||'
                "p.hostname===window.location.hostname)"
                "return pp+p.pathname+p.search;"
                'return pp+"/__amzn_host__"+p.hostname+p.pathname+p.search;}'
                "}catch(e){}return u;}"
                "var xo=XMLHttpRequest.prototype.open;"
                "XMLHttpRequest.prototype.open=function(m,u){"
                'if(typeof u==="string"){this.__origUrl=u;arguments[1]=rw(u);}'
                "return xo.apply(this,arguments);};"
                "var _xrd=Object.getOwnPropertyDescriptor("
                'XMLHttpRequest.prototype,"responseURL");'
                "if(_xrd&&_xrd.get){Object.defineProperty("
                'XMLHttpRequest.prototype,"responseURL",{'
                "get:function(){return this.__origUrl||_xrd.get.call(this);},"
                "configurable:true});}"
                "var fo=window.fetch;"
                "if(fo)window.fetch=function(i,n){"
                'var orig=typeof i==="string"?i:i;'
                'if(amazonDomain&&typeof i==="string"'
                '&&i.indexOf("/problem")!==-1){'
                "i=i.replace(/domain=[^&]+/,"
                '"domain="+encodeURIComponent(amazonDomain));}'
                'if(typeof i==="string")i=rw(i);'
                "return fo.call(this,i,n).then(function(r){"
                'if(i!==orig)Object.defineProperty(r,"url",'
                "{value:orig,configurable:true});"
                "return r;"
                "});};"
                "})();"
                "</script>"
            )
            _p_shim = (
                "<script>"
                "(function(){"
                "function mQ(s){"
                "var els=document.querySelectorAll(s);"
                "var r=Array.prototype.slice.call(els);"
                "r.click=function(fn){"
                'r.forEach(function(e){e.addEventListener("click",fn)});'
                "return r;};"
                "return r;}"
                "window.P=window.P||{when:function(){"
                "return{execute:function(fn){"
                "function go(){"
                'if(typeof CaptchaScript!=="undefined"){'
                "try{fn({$:mQ});}catch(e){"
                'console.error("[AMP] P shim execute error:",e);}'
                "}else{setTimeout(go,100);}}"
                'if(document.readyState==="loading"){'
                'document.addEventListener("DOMContentLoaded",go);'
                "}else{go();}"
                "}};"
                "}};"
                "})();"
                "</script>"
            )
            # Inject shim right before the first <script> in <head>
            _head_end = _decoded.lower().find("</head>")
            if _head_end < 0:
                _head_end = _decoded.lower().find("<body")
            _first_script = _decoded.lower().find("<script", 1)
            if _first_script > 0:
                _inject_pos = _first_script
            elif _head_end > 0:
                _inject_pos = _head_end
            else:
                _inject_pos = 0
            _decoded = (
                _decoded[:_inject_pos]
                + _aaut_ajax_proxy
                + _p_shim
                + _decoded[_inject_pos:]
            )
            # Rewrite captcha.js script src to load through the proxy
            if _awswaf_host:
                _proxy_prefix = URL(str(ctx.request.url)).path.split("/aaut/")[0]
                if not _proxy_prefix or _proxy_prefix == URL(str(ctx.request.url)).path:
                    _proxy_prefix = URL(str(ctx.request.url)).path.split("/ap/")[0]
                _old_waf_base = f"https://{_awswaf_host}/"
                _new_waf_base = f"{_proxy_prefix}/__amzn_host__{_awswaf_host}/"
                _decoded = _decoded.replace(_old_waf_base, _new_waf_base)
                _LOGGER.debug(
                    "Rewrote awswaf script src to proxy: %s -> %s",
                    _old_waf_base,
                    _new_waf_base,
                )
            ctx.body = _decoded.encode("utf-8")
            _LOGGER.debug(
                "Injected P shim + AJAX proxy into aaut/verify/cvf response (%d -> %d bytes)",
                len(ctx.response.content) if ctx.response else 0,
                len(ctx.body),
            )
        except (UnicodeDecodeError, AttributeError, TypeError) as _e:
            _LOGGER.warning("Failed to inject P shim into aaut response: %s", _e)

    async def on_page_html(self, ctx: InterceptContext) -> None:
        """Inject submit blocker + AJAX proxy into CVF full-page navigations.

        Blocks form auto-submit to give the CAPTCHA time to load, intercepts
        XHR responses to extract sessionToken, and rewrites fetch/XHR URLs
        to route through the proxy.
        """
        if not ctx.text or not ctx.response:
            return
        if "/ap/cvf/" not in URL(str(ctx.response.url)).path:
            return

        _submit_blocker_js = (
            "<script>"
            "(function(){"
            "var _origSubmit=HTMLFormElement.prototype.submit;"
            "var _blocked=true;"
            "var _pendingForm=null;"
            "HTMLFormElement.prototype.submit=function(){"
            "if(_blocked){"
            'console.log("[AMP] Form submit BLOCKED - waiting for CAPTCHA");'
            "_pendingForm=this;"
            "return;}"
            "return _origSubmit.apply(this,arguments);};"
            'document.addEventListener("submit",function(e){'
            "if(_blocked){"
            "var f=e.target;"
            "var isCVF=f&&f.querySelector&&"
            'f.querySelector("[name=cvf_aamation_response_token]");'
            "if(isCVF){"
            "e.preventDefault();e.stopPropagation();"
            'console.log("[AMP] CVF form submit BLOCKED");'
            "_pendingForm=f;"
            "return false;}"
            "}},true);"
            "var _captchaVoucher=null;"
            "var _waitingForAaut=false;"
            "var _origSend=XMLHttpRequest.prototype.send;"
            "XMLHttpRequest.prototype.send=function(){"
            "var xhr=this;"
            "if(_waitingForAaut){"
            'var xurl=xhr.__origUrl||"";'
            'if(xurl.indexOf("/aaut/verify/cvf")!==-1){'
            'xhr.addEventListener("load",function(){'
            "try{"
            'var aamResp=xhr.getResponseHeader("amz-aamation-resp");'
            "if(aamResp){"
            "var rd=JSON.parse(aamResp);"
            "if(rd.sessionToken){"
            'console.log("[AMP] Got sessionToken from aaut verify response");'
            'var tok=document.querySelector("[name=cvf_aamation_response_token]");'
            "if(tok){tok.value=rd.sessionToken;}"
            'var err=document.querySelector("[name=cvf_aamation_error_code]");'
            'if(err)err.value="";'
            'var act=document.querySelector("[name=cvf_captcha_captcha_action]");'
            'if(act)act.value="verifyAamationChallenge";'
            "if(rd.clientSideContext){"
            "var csc=decodeURIComponent(rd.clientSideContext);"
            'var cscField=document.querySelector("[name=clientSideContext]");'
            "if(!cscField){"
            "var tokEl=document.querySelector("
            '"[name=cvf_aamation_response_token]");'
            'var cvfForm=tokEl?(tokEl.closest?tokEl.closest("form")'
            ":tokEl.form):null;"
            "if(cvfForm){"
            'cscField=document.createElement("input");'
            'cscField.type="hidden";cscField.name="clientSideContext";'
            "cvfForm.appendChild(cscField);}}"
            "if(cscField){cscField.value=csc;}"
            "_waitingForAaut=false;"
            "}}}"
            'catch(e){console.error("[AMP] Error processing aaut response:",e);}'
            "});"
            "}}"
            "return _origSend.apply(this,arguments);};"
            'window.addEventListener("message",function(ev){'
            "try{var d=JSON.parse(ev.data);"
            'if(d.eventId==="aa-challenge-complete"){'
            'console.log("[AMP] CAPTCHA solved! voucher="+'
            'd.payload.substring(0,80)+"...");'
            "_blocked=false;"
            "_captchaVoucher=d.payload;"
            "_waitingForAaut=true;"
            "setTimeout(function(){"
            "if(_waitingForAaut){"
            'console.log("[AMP] ACIC timeout - submitting form '
            'with voucher as fallback");'
            "var tok=document.querySelector("
            '"[name=cvf_aamation_response_token]");'
            "if(tok){"
            "tok.value=_captchaVoucher;}"
            'var err=document.querySelector("[name=cvf_aamation_error_code]");'
            'if(err)err.value="";'
            "var act=document.querySelector("
            '"[name=cvf_captcha_captcha_action]");'
            'if(act)act.value="verifyAamationChallenge";'
            "if(_pendingForm){_origSubmit.call(_pendingForm);}"
            "_waitingForAaut=false;"
            "}},5000);"
            "}"
            'if(d.eventId==="aa-challenge-loaded"){'
            "}"
            "}catch(e){}});"
            "setTimeout(function(){"
            "if(_blocked){"
            'console.log("[AMP] Fallback timeout - unblocking form submit");'
            "_blocked=false;"
            "if(_pendingForm){_origSubmit.call(_pendingForm);}"
            "}},15000);"
            "})();"
            "</script>"
        )
        _ajax_proxy_js = (
            "<script>"
            "(function(){"
            'var pp=window.location.pathname.split("/ap/")[0];'
            "function rw(u){"
            "try{var p=new URL(u,window.location.href);"
            "if(p.hostname.match(/\\.(amazon\\.(com|it|co\\.uk|de|nl|com\\.be|pl|se|fr|es|co\\.jp"
            "|ca|com\\.au|in|com\\.br)|awswaf\\.com|amazoncognito\\.com"
            "|ssl-images-amazon\\.com)$/)){"
            'if(p.hostname==="www.amazon.com"||'
            "p.hostname===window.location.hostname)"
            "return pp+p.pathname+p.search;"
            'return pp+"/__amzn_host__"+p.hostname+p.pathname+p.search;'
            "}}catch(e){}return u;}"
            "var xo=XMLHttpRequest.prototype.open;"
            "XMLHttpRequest.prototype.open=function(m,u){"
            'if(typeof u==="string"){this.__origUrl=u;arguments[1]=rw(u);}'
            "return xo.apply(this,arguments);};"
            "var _xrd=Object.getOwnPropertyDescriptor("
            'XMLHttpRequest.prototype,"responseURL");'
            "if(_xrd&&_xrd.get){Object.defineProperty("
            'XMLHttpRequest.prototype,"responseURL",{'
            "get:function(){return this.__origUrl||_xrd.get.call(this);},"
            "configurable:true});}"
            "var fo=window.fetch;"
            "if(fo)window.fetch=function(i,n){"
            'var orig=typeof i==="string"?i:i;'
            'if(typeof i==="string")i=rw(i);'
            "if(i===orig)return fo.call(this,i,n);"
            'console.log("[AMP] fetch rewrite:",'
            'orig.substring(0,80),"->",i.substring(0,80));'
            "return fo.call(this,i,n).then(function(r){"
            'Object.defineProperty(r,"url",'
            "{value:orig,configurable:true});"
            "return r;});};"
            "var sb=navigator.sendBeacon;"
            "if(sb)navigator.sendBeacon=function(u,d){"
            "return sb.call(this,rw(u),d);};"
            "})();"
            "</script>"
        )
        text = ctx.text
        _script_pos = text.lower().find("<script")
        if _script_pos >= 0:
            text = (
                text[:_script_pos]
                + _submit_blocker_js
                + _ajax_proxy_js
                + text[_script_pos:]
            )
            _LOGGER.debug(
                "Injected submit blocker + AJAX proxy into CVF page (%s)",
                ctx.response.url,
            )
            ctx.text = text
