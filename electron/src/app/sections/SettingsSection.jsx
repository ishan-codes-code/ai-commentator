import { useEffect, useState } from 'react';
import { Check, KeyRound, LoaderCircle, RotateCcw, Save, ShieldCheck, Wifi } from 'lucide-react';
import { Button } from '@/components/ui/button.jsx';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card.jsx';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs.jsx';
import { DEFAULT_SETTINGS } from '../../settings-defaults.mjs';

const copy = (v) => JSON.parse(JSON.stringify(v));

export default function SettingsSection() {
  const [settings, setSettings] = useState(copy(DEFAULT_SETTINGS));
  const [key, setKey] = useState('');
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [credentialAvailable, setCredentialAvailable] = useState(false);
  const [models, setModels] = useState([]);
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [step, setStep] = useState(0);
  const onboarding = !settings.onboardingCompleted;

  useEffect(() => {
    let alive = true;
    window.api.loadSettings().then((result) => {
      if (!alive || !result?.ok) { if (alive) setError(result?.error ?? 'Could not load settings.'); return; }
      setSettings(result.settings); setKeyConfigured(result.keyConfigured); setCredentialAvailable(result.credentialAvailable);
    }).catch(() => { if (alive) setError('Could not load settings.'); });
    return () => { alive = false; };
  }, []);

  const update = (group, field, value) => setSettings((current) => ({ ...current, [group]: { ...current[group], [field]: value } }));
  const save = async (complete = false) => {
    setBusy('save'); setError(''); setNotice('');
    try {
      const result = await window.api.saveSettings({ ...settings, onboardingCompleted: complete || settings.onboardingCompleted });
      if (!result?.ok) throw new Error(result?.error ?? 'Could not save settings.');
      setSettings(result.settings); setNotice('Settings saved.');
    } catch (e) { setError(e.message); } finally { setBusy(''); }
  };
  const setCredential = async () => {
    setBusy('key'); setError(''); setNotice('');
    try {
      const result = await window.api.setProviderKey(key);
      if (!result?.ok) throw new Error(result?.error ?? 'Could not save API key.');
      setKey(''); setKeyConfigured(true); setNotice('API key stored securely.');
    } catch (e) { setError(e.message); } finally { setBusy(''); }
  };
  const deleteCredential = async () => {
    setBusy('key'); setError('');
    try { const result = await window.api.deleteProviderKey(); if (!result?.ok) throw new Error(result.error); setKeyConfigured(false); setNotice('API key removed.'); }
    catch (e) { setError(e.message); } finally { setBusy(''); }
  };
  const providerAction = async (action) => {
    setBusy(action); setError(''); setNotice('');
    try {
      const result = await window.api.providerSetup(action, settings);
      if (!result?.ok) throw new Error(result?.error ?? 'Provider request failed.');
      if (action === 'models') setModels((result.models ?? []).map((item) => item.id));
      if (action === 'verify-vision') setSettings((current) => ({ ...current, provider: { ...current.provider, modelVisionVerified: true } }));
      setNotice(action === 'models' ? `Found ${result.models.length} models. Vision support is unverified until checked.` : action === 'verify-vision' ? 'Vision input verified for the selected model.' : 'Provider connection succeeded.');
    } catch (e) { setError(e.message); } finally { setBusy(''); }
  };
  const restorePrompts = () => setSettings((current) => ({ ...current, prompt: copy(DEFAULT_SETTINGS.prompt), game: { ...current.game, context: '' } }));

  return <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 pb-8">
    {onboarding && <Card><CardHeader><CardTitle>Welcome to AI Commentator</CardTitle><CardDescription>Set up a provider and your commentary style. You can skip optional steps and change everything later.</CardDescription></CardHeader><CardContent className="flex flex-wrap items-center gap-2"><span className="mr-auto text-[12px] text-muted-foreground">Step {step + 1} of 4 · Provider → Model → Game → Style</span><Button variant="outline" onClick={() => save(true)}>Skip setup</Button><Button onClick={() => setStep(Math.min(3, step + 1))}>Continue</Button></CardContent></Card>}
    <Tabs defaultValue={onboarding ? ['provider', 'provider', 'game', 'prompts'][step] : 'provider'} key={`${onboarding}:${step}`}>
      <TabsList><TabsTrigger value="provider">Provider</TabsTrigger><TabsTrigger value="game">Game</TabsTrigger><TabsTrigger value="prompts">Commentary</TabsTrigger></TabsList>
      <TabsContent value="provider"><Card><CardHeader><CardTitle>AI provider</CardTitle><CardDescription>One OpenAI-compatible provider is active. Baseten and Muse defaults are preserved.</CardDescription></CardHeader><CardContent className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5 text-[13px]">API type<select className="h-9 rounded-md border border-input bg-background px-3" value={settings.provider.type} onChange={(e) => update('provider', 'type', e.target.value)}><option value="openai-compatible">OpenAI-compatible API</option></select></label>
        <label className="flex flex-col gap-1.5 text-[13px]">API base URL<input className="h-9 rounded-md border border-input bg-background px-3" value={settings.provider.baseUrl} onChange={(e) => { update('provider', 'baseUrl', e.target.value); update('provider', 'modelVisionVerified', false); }} /></label>
        <label className="flex flex-col gap-1.5 text-[13px]">API key<input type="password" autoComplete="new-password" className="h-9 rounded-md border border-input bg-background px-3" value={key} placeholder={keyConfigured ? 'Key stored securely' : 'Enter API key'} onChange={(e) => setKey(e.target.value)} /></label>
        <div className="flex flex-wrap items-center gap-2"><Button onClick={setCredential} disabled={!key.trim() || !!busy}><KeyRound data-icon="inline-start" />Save key</Button>{keyConfigured && <Button variant="outline" onClick={deleteCredential} disabled={!!busy}>Remove key</Button>}<span className="text-[12px] text-muted-foreground">{credentialAvailable ? 'OS secure storage available' : 'Secure storage unavailable'}</span></div>
        <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={!keyConfigured || !!busy} onClick={() => providerAction('connection')}><Wifi data-icon="inline-start" />Test connection</Button><Button variant="outline" disabled={!keyConfigured || !!busy} onClick={() => providerAction('models')}>Discover models</Button></div>
        <label className="flex flex-col gap-1.5 text-[13px]">Vision model<select className="h-9 rounded-md border border-input bg-background px-3" value={settings.provider.model} onChange={(e) => setSettings((s) => ({ ...s, provider: { ...s.provider, model: e.target.value, modelVisionVerified: false } }))}>{![...new Set([settings.provider.model, ...models])].map((model) => <option key={model} value={model}>{model}{model === settings.provider.model && settings.provider.modelVisionVerified ? ' · vision verified' : models.includes(model) ? ' · capability unverified' : ''}</option>)}</select></label>
        <div className="flex flex-wrap items-center gap-2"><Button variant="outline" disabled={!keyConfigured || !!busy} onClick={() => providerAction('verify-vision')}><ShieldCheck data-icon="inline-start" />Verify image support</Button><span className="text-[12px] text-muted-foreground">{settings.provider.modelVisionVerified ? 'Vision verified' : 'Model capability not verified. Check sends a small image request and may incur charges.'}</span></div>
        {onboarding && <div className="flex gap-2">{step > 0 && <Button variant="outline" onClick={() => setStep(0)}>Back</Button>}<Button onClick={() => setStep(step === 0 ? 1 : 2)}>{step === 0 ? 'Next: connection and model' : 'Next: game'}</Button></div>}
      </CardContent></Card></TabsContent>
      <TabsContent value="game"><Card><CardHeader><CardTitle>Game context</CardTitle><CardDescription>Game settings stay separate from provider credentials. Only supported game contexts are accepted.</CardDescription></CardHeader><CardContent className="flex flex-col gap-4"><label className="flex flex-col gap-1.5 text-[13px]">Game<select className="h-9 rounded-md border border-input bg-background px-3" value={settings.game.id} onChange={(e) => update('game', 'id', e.target.value)}><option value="free-fire">Free Fire</option></select></label><label className="flex flex-col gap-1.5 text-[13px]">Additional game context<textarea className="min-h-24 rounded-md border border-input bg-background px-3 py-2" value={settings.game.context} maxLength={2000} onChange={(e) => update('game', 'context', e.target.value)} placeholder="Optional background context. The model must still rely on visible evidence." /></label>{onboarding && <div className="flex gap-2"><Button variant="outline" onClick={() => setStep(1)}>Back</Button><Button onClick={() => setStep(3)}>Next: commentary style</Button></div>}</CardContent></Card></TabsContent>
      <TabsContent value="prompts"><Card><CardHeader><CardTitle>Commentary style</CardTitle><CardDescription>These preferences guide wording only. Observation schemas, claim gates, and factuality checks remain protected.</CardDescription></CardHeader><CardContent className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5 text-[13px]">Personality<input className="h-9 rounded-md border border-input bg-background px-3" value={settings.prompt.personality} maxLength={80} onChange={(e) => update('prompt', 'personality', e.target.value)} /></label>
        <label className="flex flex-col gap-1.5 text-[13px]">Tone<select className="h-9 rounded-md border border-input bg-background px-3" value={settings.prompt.tone} onChange={(e) => update('prompt', 'tone', e.target.value)}>{['energetic', 'measured', 'dramatic', 'calm'].map((v) => <option key={v}>{v}</option>)}</select></label>
        <label className="flex flex-col gap-1.5 text-[13px]">Language<input className="h-9 rounded-md border border-input bg-background px-3" value={settings.prompt.language} maxLength={80} onChange={(e) => update('prompt', 'language', e.target.value)} /></label>
        <label className="flex flex-col gap-1.5 text-[13px]">Custom style instructions<textarea className="min-h-24 rounded-md border border-input bg-background px-3 py-2" value={settings.prompt.instructions} maxLength={2000} onChange={(e) => update('prompt', 'instructions', e.target.value)} placeholder="Style preferences only. Evidence and uncertainty rules cannot be overridden." /></label>
        <div className="rounded-md border border-border bg-muted p-3 text-[12px] text-muted-foreground"><strong className="text-foreground">Prompt preview</strong><p className="mt-2">You are a concise Free Fire commentator. Use {settings.prompt.tone} language in {settings.prompt.language || 'the selected language'} as a {settings.prompt.personality || 'commentator'}. Custom style: {settings.prompt.instructions || 'none'}. Only gated evidence may be stated; uncertainty stays explicit.</p></div>
        <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={restorePrompts}><RotateCcw data-icon="inline-start" />Restore defaults</Button>{onboarding && <Button variant="outline" onClick={() => setStep(2)}>Back</Button>}<Button onClick={() => save(onboarding)} disabled={!!busy}><Save data-icon="inline-start" />Save settings</Button></div>
      </CardContent></Card></TabsContent>
    </Tabs>
    {notice && <p role="status" className="text-[13px] text-muted-foreground"><Check className="mr-1 inline size-4" />{notice}</p>}{error && <p role="alert" className="text-[13px] text-destructive">{error}</p>}{busy && <p role="status" className="text-[12px] text-muted-foreground"><LoaderCircle className="mr-1 inline size-4 animate-spin" />Working…</p>}
  </div>;
}
