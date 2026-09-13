export async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
      return true;
    } catch {
      return false;
    } finally {
      document.body.removeChild(ta);
    }
  }
}

export function defaultChoices(recommendation, system) {
  const recModel = recommendation.models.find((m) => m.recommended);
  const nemotronDefault =
    recommendation.nemotron.mode === 'default'
      ? true
      : recommendation.nemotron.mode === 'optional'
        ? false
        : false;

  return {
    ai_enabled: recommendation.ai.recommended,
    model: recModel?.name ?? recommendation.models.find((m) => m.fits)?.name ?? null,
    nemotron_enabled: nemotronDefault,
    ngc_key: null,
    ngc_key_later: false,
    network_scope: 'local',
    adopt_existing: system?.existing?.kind === 'manual',
  };
}

export const SECURITY_URL =
  'https://github.com/SaltShaker87/resident-evaluations-auc/blob/main/SECURITY.md';
