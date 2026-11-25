import { Robot, SquaresFour, TerminalWindow } from '@phosphor-icons/react';

const logos: Record<string, string> = {
  codex: new URL('./assets/agents/codex.svg', import.meta.url).href,
  claude: new URL('./assets/agents/claude.svg', import.meta.url).href,
  pi: new URL('./assets/agents/pi.svg', import.meta.url).href,
  hermes: new URL('./assets/agents/hermes.svg', import.meta.url).href,
  antigravity: new URL('./assets/agents/antigravity.png', import.meta.url).href,
  opencode: new URL('./assets/agents/opencode.svg', import.meta.url).href,
};

/** Decorative beside an Agent name, or labelled when used on its own. */
export function AgentLogo({ agent, size = 'md', label }: {
  agent: string; size?: 'sm' | 'md' | 'avatar'; label?: string;
}) {
  const source = logos[agent];
  const Fallback = agent === 'all' ? SquaresFour : agent === 'shell' ? TerminalWindow : Robot;
  return <span className={`agent-logo agent-logo-${size} agent-logo-${source ? agent : 'generic'}`}
    role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
    {source ? <img src={source} alt="" draggable={false} /> : <Fallback aria-hidden="true" />}
  </span>;
}
