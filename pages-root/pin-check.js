/* Continuum Pages static export — access gate (architecture Amendment A1).
 *
 * This is the ONLY board script in the export index.html, loaded blocking in <head>.
 * It holds the full gate logic: hash compare against the PIN_HASH_HEX const below,
 * the sessionStorage unlock check/write, the bounded wrong-code backoff, focus
 * management, and appending ./app.js as a module on unlock. The renderer (app.js)
 * never loads before unlock and knows nothing about this gate.
 *
 * G3/D13: the plaintext code CANNOT exist in this file by construction — only the
 * SHA-256 hex of its exact UTF-8 bytes. Re-hash procedure: PIN-GATE-SPEC.md.
 * The gate is honest friction over synthetic data, not confidentiality.
 */
const PIN_HASH_HEX = "00aa6525c6fde5a50d38168637e61f25b9ffb28c246503e5c6032950cb59c84b";

(function () {
  'use strict'

  var KEY = 'continuum-unlocked'
  var docEl = document.documentElement
  var sessionUnlocked = false
  try { sessionUnlocked = window.sessionStorage.getItem(KEY) === '1' } catch (e) { sessionUnlocked = false }

  // The lock is the attribute: its absence means unlocked. Set before first paint.
  if (!sessionUnlocked) docEl.setAttribute('data-gate', 'locked')

  var attempts = 0
  var lastAttemptAt = 0
  var ticking = null

  function gateRoot() { return document.getElementById('gate-root') }
  function field() { return document.getElementById('gate-input') }
  function button() { return document.getElementById('gate-submit') }
  function card() { return document.querySelector('.c-gate-card') }

  function setState(name) {
    if (name) gateRoot().setAttribute('data-state', name)
    else gateRoot().removeAttribute('data-state')
  }

  // statusMsg: one short sentence, words first (design §2.3); cls: is-error | is-wait | ''.
  function say(text, cls, live) {
    var st = document.getElementById('gate-status')
    st.setAttribute('aria-live', live ? 'polite' : 'off')
    st.textContent = text
    st.className = 'c-gate-status' + (cls ? ' ' + cls : '')
  }

  function focusField() { field().focus({ preventScroll: true }) }

  function sha256Hex(text) {
    var data = new TextEncoder().encode(text)
    return crypto.subtle.digest('SHA-256', data).then(function (buf) {
      var b = new Uint8Array(buf), out = ''
      for (var i = 0; i < b.length; i++) out += ('0' + b[i].toString(16)).slice(-2)
      return out
    })
  }

  function loadBoard() {
    var s = document.createElement('script')
    s.type = 'module'
    s.src = './app.js'
    document.body.appendChild(s)
    // Unlock focus: filter on desktop, the shell below 640px (design §2.5/§2.8).
    setTimeout(function () {
      var f = document.getElementById('filter')
      if (window.innerWidth > 640 && f) f.focus({ preventScroll: true })
      else {
        var w = document.querySelector('.wrap')
        if (w) { w.setAttribute('tabindex', '-1'); w.focus({ preventScroll: true }) }
      }
    }, 0)
  }

  // Bounded linear wait (D14): attempts 1-3 free, then 5/10/20/40 s, flat 40 s cap;
  // the counter resets on success or after 60 s without attempts (logic, not visuals).
  function waitSecondsOwed() {
    var over = attempts - 3
    if (over <= 0) return 0
    return Math.min(40, 5 * Math.pow(2, Math.min(over - 1, 3)))
  }

  function startWait(seconds) {
    setState('waiting')
    field().disabled = true
    button().disabled = true
    card().focus({ preventScroll: true })
    var remaining = seconds
    // Announced once with the full duration; the tick is visual only.
    say('Too many attempts. Try again in ' + remaining + ' seconds.', 'is-wait', true)
    ticking = window.setInterval(function () {
      remaining -= 1
      if (remaining > 0) {
        say('Too many attempts. Try again in ' + remaining + ' seconds.', 'is-wait', false)
      } else {
        window.clearInterval(ticking)
        ticking = null
        field().disabled = false
        button().disabled = false
        setState('idle')
        say('You can try again now.', '', true)
        focusField()
      }
    }, 1000)
  }

  function onSubmit(ev) {
    ev.preventDefault()
    if (ticking) return
    var value = field().value
    if (value === '') {
      // Empty submit: no attempt is counted, the field keeps focus.
      say('Enter the access code to continue.', '', true)
      focusField()
      return
    }
    var now = Date.now()
    if (now - lastAttemptAt > 60000) attempts = 0
    lastAttemptAt = now
    attempts += 1
    sha256Hex(value).then(function (hex) {
      if (hex === PIN_HASH_HEX) {
        attempts = 0
        try { window.sessionStorage.setItem(KEY, '1') } catch (e) { /* per-tab session */ }
        docEl.removeAttribute('data-gate')
        loadBoard()
        return
      }
      field().value = ''
      var owed = waitSecondsOwed()
      if (owed > 0) {
        startWait(owed)
      } else {
        setState('error')
        say('That code did not match. Check it and try again.', 'is-error', true)
        focusField()
      }
    }).catch(function () {
      setState('error')
      say('This page needs a secure context (https or localhost) to check the code.', 'is-error', true)
    })
  }

  function trapTab(ev) {
    if (ev.key !== 'Tab' || docEl.getAttribute('data-gate') !== 'locked') return
    ev.preventDefault()
    if (gateRoot().getAttribute('data-state') === 'waiting') return   // focus stays on the card
    // The screen has exactly two controls: cycle input -> submit -> input (design §2.9).
    var a = document.activeElement
    var next = ev.shiftKey
      ? (a === button() ? field() : button())
      : (a === field() ? button() : field())
    next.focus({ preventScroll: true })
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (!sessionUnlocked) {
      var form = document.getElementById('gate-form')
      form.addEventListener('submit', onSubmit)
      form.addEventListener('keydown', trapTab)
      if (!window.crypto || !crypto.subtle) {
        field().disabled = true
        button().disabled = true
        setState('error')
        say('This page needs a secure context (https or localhost) to check the code.', 'is-error', true)
        return
      }
      setState('idle')
      focusField()
    } else {
      // Session already unlocked: straight to the board, no gate frame.
      loadBoard()
    }
  })
})()
