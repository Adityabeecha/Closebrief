"""Login gate (QA_REVIEW_2026-10.md "Minor / polish"): a real form so Enter
submits and password managers fill it, field errors that say how to fix them,
a busy submit button, and a visible guest entry when the server allows guests."""

import re
from pathlib import Path

from test_frontend_followup import run_js

INDEX = Path(__file__).parents[1] / "ui/web/index.html"
_AUTH = ("function toggleAuthMode(", "async function onSession(")

_DOM = """
  const els={};
  const mk=id=>({id,value:'',textContent:'',style:{},disabled:false,attrs:{},innerHTML:'',
    setAttribute(k,v){this.attrs[k]=String(v);},removeAttribute(k){delete this.attrs[k];},
    focus(){focused=id;}});
  let focused=null;
  const $=id=>(els[id]??=mk(id));
"""


def test_login_fields_live_in_a_real_form_so_enter_submits():
    html = INDEX.read_text(encoding="utf-8")
    form = re.search(r'<form id="lg-form"[^>]*>(.*?)</form>', html, re.S)
    assert form, "login fields must be inside a <form>"
    assert 'onsubmit="event.preventDefault();submitAuth()"' in form.group(0)
    body = form.group(1)
    assert 'id="lg-email"' in body and 'id="lg-pass"' in body
    assert re.search(r'<button type="submit"[^>]*id="lg-btn"', body)
    # Enter now comes from the form; the old per-input handlers would double-submit.
    assert "onkeydown" not in body
    assert 'autocomplete="email"' in body and 'autocomplete="current-password"' in body
    # No fake-filled "••••" placeholder on the password.
    pw = re.search(r'<input id="lg-pass"[^>]*>', body).group(0)
    assert "placeholder" not in pw
    assert 'aria-describedby="lg-pass-err"' in pw


def test_field_errors_say_how_to_fix_them():
    run_js(*_AUTH, "", """
      assert.deepEqual(validateAuth('','', 'signin'),{email:'Enter your work email.',pass:'Enter your password.'});
      assert.match(validateAuth('ana@acme','x','signin').email,/@ and a domain/);
      assert.deepEqual(validateAuth('ana@acme.com','abc','signin'),{});
      assert.equal(validateAuth('ana@acme.com','abc','signup').pass,'Use at least 6 characters.');
      assert.match(authErrorMessage({message:'Invalid login credentials'}),/don't match/);
      assert.match(authErrorMessage({message:'Email not confirmed'}),/Confirm your email/);
      assert.equal(authErrorMessage({message:'Something odd'}),'Something odd');
      assert.match(authErrorMessage(null),/Try again/);
    """)


def test_invalid_form_marks_fields_and_never_calls_auth():
    run_js(*_AUTH, _DOM + """
      let authMode='signin',calls=0;
      const sb={auth:{signInWithPassword:async()=>{calls++;return {data:{},error:null};}}};
      const onSession=()=>{};
    """, """
      $('lg-email').value='nope';$('lg-pass').value='';
      await submitAuth();
      assert.equal(calls,0);
      assert.match($('lg-email-err').textContent,/@ and a domain/);
      assert.equal($('lg-email').attrs['aria-invalid'],'true');
      assert.equal($('lg-pass-err').textContent,'Enter your password.');
      assert.equal(focused,'lg-email');
    """)


def test_submit_button_is_busy_while_signing_in_and_ignores_repeats():
    run_js(*_AUTH, _DOM + """
      let authMode='signin',calls=0,finish;
      const sb={auth:{signInWithPassword:()=>{calls++;return new Promise(r=>{finish=r;});}}};
      const onSession=()=>{};
    """, """
      $('lg-email').value='ana@acme.com';$('lg-pass').value='secret1';
      const first=submitAuth();
      await Promise.resolve();
      assert.equal($('lg-btn').disabled,true);
      assert.equal($('lg-btn').textContent,'Signing in…');
      assert.equal($('lg-btn').attrs['aria-busy'],'true');
      await submitAuth();                       // a second Enter while busy
      assert.equal(calls,1);
      finish({data:{},error:{message:'Invalid login credentials'}});
      await first;
      assert.equal($('lg-btn').disabled,false);
      assert.equal($('lg-btn').textContent,'Sign in');
      assert.match($('lg-err').textContent,/don't match/);
      assert.equal($('lg-err').style.display,'block');
    """)


def test_sign_up_mode_asks_password_managers_for_a_new_password():
    run_js(*_AUTH, _DOM + "let authMode='signin';", """
      toggleAuthMode();
      assert.equal($('lg-pass').attrs.autocomplete,'new-password');
      assert.equal($('lg-btn').textContent,'Create account');
      assert.match($('lg-toggle').innerHTML,/<button type="button"/);
      toggleAuthMode();
      assert.equal($('lg-pass').attrs.autocomplete,'current-password');
    """)


def test_guest_entry_opens_the_app_read_only():
    run_js("let demoSession=false, guestSession=false", "function exitDemo(", _DOM + """
      let signedIn=true,appStarted=true,started=0;
      const document={body:{classList:{add(){}}}};
      const hideLogin=()=>{},startApp=()=>{started++;};
    """, """
      enterGuest();
      assert.equal(guestSession,true);assert.equal(demoSession,false);
      assert.equal(signedIn,false);assert.equal(started,1);
      assert.match($('demo-bar-text').innerHTML,/guest/);
      assert.equal($('demo-bar-exit').textContent,'Sign in');
    """)
    run_js("async function startApp(", "function applyRole(", """
      let signedIn=false,demoSession=false,guestSession=true,currentUser=null,applied=null;
      const applyRole=u=>{applied=u;};
      const loadPeriods=()=>({then(){}});
    """, """
      await startApp();
      assert.deepEqual(applied,{email:'Guest',role:'viewer'});
    """)


def test_guest_button_only_shows_when_the_server_allows_guests():
    html = INDEX.read_text(encoding="utf-8")
    assert re.search(r'<button type="button" class="lg-guest" id="lg-guest" style="display:none"', html)
    run_js("async function boot(", "/* ================= Google Sign-In", _DOM + """
      let appCfg={},sb=null,signedIn=false,appStarted=false;
      const fetch=async()=>({json:async()=>({auth_enabled:false,allow_guest:true})});
      const initSentry=()=>{},hideLogin=()=>{},startApp=()=>{},takeLegacyToken=()=>null;
      $('lg-guest').style.display='none';$('lg-guest-note').style.display='none';
    """, """
      await boot();
      assert.equal($('lg-guest').style.display,'');
      assert.equal($('lg-guest-note').style.display,'');
    """)
