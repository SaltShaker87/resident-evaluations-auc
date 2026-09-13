import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import {
  mockCancelAction,
  mockCheckForUpdate,
  mockDetectSystem,
  mockGetLog,
  mockOpenApp,
  mockOpenUrl,
  mockReadState,
  mockRecommend,
  mockRunAction,
  mockSubscribe,
} from './mock.js';

const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

export function detectSystem() {
  if (isTauri()) return invoke('detect_system');
  return mockDetectSystem();
}

export function recommend(system) {
  if (isTauri()) return invoke('recommend', { system });
  return mockRecommend(system);
}

export function readState() {
  if (isTauri()) return invoke('read_state');
  return mockReadState();
}

export function checkForUpdate() {
  if (isTauri()) return invoke('check_for_update');
  return mockCheckForUpdate();
}

export function runAction(action) {
  if (isTauri()) return invoke('run_action', { action });
  return mockRunAction(action);
}

export function cancelAction() {
  if (isTauri()) return invoke('cancel_action');
  return mockCancelAction();
}

export function getLog() {
  if (isTauri()) return invoke('get_log');
  return mockGetLog();
}

export function openApp() {
  if (isTauri()) return invoke('open_app');
  return mockOpenApp();
}

export function openUrl(url) {
  if (isTauri()) return invoke('open_url', { url });
  return mockOpenUrl(url);
}

export function subscribe({ onStep, onLog, onPreflight, onFinished }) {
  if (!isTauri()) {
    return mockSubscribe({ onStep, onLog, onPreflight, onFinished });
  }

  const unsubs = [];
  const setup = async () => {
    unsubs.push(await listen('auc://step', (e) => onStep?.(e.payload)));
    unsubs.push(await listen('auc://log', (e) => onLog?.(e.payload)));
    unsubs.push(await listen('auc://preflight', (e) => onPreflight?.(e.payload)));
    unsubs.push(await listen('auc://finished', (e) => onFinished?.(e.payload)));
  };
  setup();

  return () => {
    for (const u of unsubs) u();
  };
}
