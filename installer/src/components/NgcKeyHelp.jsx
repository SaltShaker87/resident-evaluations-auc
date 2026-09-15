import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { openUrl } from '../backend.js';

export const NGC_URL = 'https://ngc.nvidia.com';
export const NGC_EMBED_URL =
  'https://catalog.ngc.nvidia.com/orgs/nim/teams/nvidia/containers/nemotron-3-embed-1b';
export const NGC_RERANK_URL =
  'https://catalog.ngc.nvidia.com/orgs/nim/teams/nvidia/containers/llama-nemotron-rerank-vl-1b-v2';

/**
 * The "Open NVIDIA account site" button, with step-by-step instructions
 * underneath that fold away until asked for. Written for someone who has
 * never made an account on a developer website: every step is one thing to
 * click or type, and the two things that go wrong (missing the verification
 * email; losing the key, which NVIDIA shows exactly once) are called out.
 */
export default function NgcKeyHelp() {
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <div className="ngc-help">
      <button
        type="button"
        className="btn btn--secondary btn--sm"
        onClick={() => openUrl(NGC_URL)}
      >
        Open NVIDIA account site
      </button>
      <button
        type="button"
        className="collapsible__toggle ngc-help__toggle"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Chevron size={16} aria-hidden />
        {open ? 'Hide the step-by-step instructions' : 'Show me what to do, step by step'}
      </button>
      {open && (
        <ol className="ngc-steps">
          <li>
            <strong>Open the NVIDIA site</strong> with the button above. It opens in your
            browser; keep this installer window open too.
          </li>
          <li>
            <strong>Create a free account.</strong> Click <em>Sign In / Sign Up</em> in the top
            right corner, then <em>Create Account</em>. Use an email address you can check right
            now.
          </li>
          <li>
            <strong>Verify your email.</strong> NVIDIA sends a message with a link or code. Open
            it and follow it. If nothing arrives in a few minutes, check your junk or spam folder.
          </li>
          <li>
            <strong>Answer the short set-up questions</strong> NVIDIA asks after your first sign
            in (your name, and what you plan to use the account for). Any honest answer is fine;{' '}
            <em>Healthcare</em> or <em>Education</em> fits AUC.
          </li>
          <li>
            <strong>Open your account menu.</strong> Back on the NVIDIA site, click your name or
            initials in the top right corner.
          </li>
          <li>
            <strong>Choose <em>Setup</em></strong> (sometimes shown as <em>Account Settings</em>
            or <em>API Keys</em>) from that menu.
          </li>
          <li>
            <strong>Click <em>Generate API Key</em></strong> (or <em>Generate Personal Key</em>).
            If it asks which services the key is for, tick <em>NGC Catalog</em>. A key without
            that box ticked cannot download the models.
          </li>
          <li>
            <strong>Open the two model pages once</strong> and accept the terms NVIDIA shows if
            it asks. Without that, a valid key still gets “access denied.” Keep this installer
            window open.
            <ul>
              <li>
                <button type="button" className="link-btn" onClick={() => openUrl(NGC_EMBED_URL)}>
                  nemotron-3-embed-1b
                </button>
              </li>
              <li>
                <button type="button" className="link-btn" onClick={() => openUrl(NGC_RERANK_URL)}>
                  llama-nemotron-rerank-vl-1b-v2
                </button>
              </li>
            </ul>
          </li>
          <li>
            <strong>Copy the key and keep it safe.</strong> It is a long line of letters and
            numbers that starts with <code>nvapi-</code>. NVIDIA shows it only once: if you
            close the page without copying it, you will have to generate a new one. Paste it
            into the box below, and also write it down or save it somewhere you will find again.
          </li>
        </ol>
      )}
    </div>
  );
}
