import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Check, Copy, Cube } from '@phosphor-icons/react';

function CopyLink({ href }: { href: string }) {
  const [state, setState] = useState('复制链接');
  return <button className="copy-button" title={state} aria-label={state} onClick={() => {
    void navigator.clipboard.writeText(href).then(() => setState('已复制')).catch(() => setState('复制失败'));
  }}>{state === '已复制' ? <Check /> : <Copy />}</button>;
}

export default function Markdown({ text }: { text: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
    img: ({ alt }) => <span className="remote-image"><Cube />图片引用：{alt || '未命名'}（不会自动加载远程内容）</span>,
    a: ({ href, children }) => <span className="safe-link">{children}{href && <CopyLink href={href} />}</span>,
    pre: ({ children }) => <div className="code-block"><div className="code-label">SOURCE / CODE</div><pre>{children}</pre></div>,
    table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
  }}>{text}</ReactMarkdown>;
}
