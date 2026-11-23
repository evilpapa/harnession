import { useEffect, useRef } from 'react';
import { Terminal, type ITheme } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { useQueryClient } from '@tanstack/react-query';
import { acknowledge, resizePty, subscribe, unsubscribe, writePty } from './api';
import type { ColorTheme, Run } from './types';
import '@xterm/xterm/css/xterm.css';

const terminalThemes: Record<ColorTheme, ITheme> = {
  dark: {
    background: '#080e16', foreground: '#dce7f4', cursor: '#79e2f2',
    selectionBackground: '#79e2f233', green: '#cbf28a', cyan: '#79e2f2',
    red: '#ff9fa9', yellow: '#f4c785',
  },
  light: {
    background: '#f8fafc', foreground: '#263b52', cursor: '#006578',
    selectionBackground: '#00657833',
    black: '#213246', red: '#a42439', green: '#38701d', yellow: '#805000',
    blue: '#204d9b', magenta: '#72458f', cyan: '#006578', white: '#52677c',
    brightBlack: '#54697c', brightRed: '#b32b42', brightGreen: '#397322', brightYellow: '#8a5600',
    brightBlue: '#2757a8', brightMagenta: '#794595', brightCyan: '#006c76', brightWhite: '#344a61',
  },
};

export function TerminalPane({ run, visible, theme, onError }: {
  run: Run; visible: boolean; theme: ColorTheme; onError: (message: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const terminal = useRef<Terminal | null>(null);
  const fit = useRef<FitAddon | null>(null);
  const errorRef = useRef(onError);
  errorRef.current = onError;
  const queryClient = useQueryClient();
  useEffect(() => {
    const instance = new Terminal({
      fontFamily: '"JetBrains Mono", "Noto Sans Mono", monospace',
      fontSize: 13, lineHeight: 1.4, cursorBlink: false, scrollback: 5000,
        allowProposedApi: false, theme: terminalThemes.dark,
    });
    const addon = new FitAddon();
    instance.loadAddon(addon);
    instance.open(host.current!);
    terminal.current = instance; fit.current = addon;
    let disposed = false, lastSeq = 0, writes = Promise.resolve();
    function send(bytes: number[]) {
      writes = writes.then(() => writePty(run.id, bytes)).then(() => undefined)
        .catch((error) => errorRef.current(String(error)));
    }
    const input = instance.onData((value) => send(Array.from(new TextEncoder().encode(value))));
    const binary = instance.onBinary((value) => send(Array.from(value, (char) => char.charCodeAt(0) & 255)));
    const observer = new ResizeObserver(() => {
      if (!host.current?.clientWidth || !host.current?.clientHeight) return;
      addon.fit();
      void resizePty(run.id, instance.cols, instance.rows).catch((error) => errorRef.current(String(error)));
    });
    observer.observe(host.current!);
    void subscribe(run.id, 0, (event) => {
      if (disposed) return;
      if (event.seq <= lastSeq) { void acknowledge(run.id, event.seq); return; }
      const done = () => {
        lastSeq = event.seq;
        void acknowledge(run.id, event.seq).catch(() => undefined);
      };
      if (event.kind === 'output') {
        const bytes = Uint8Array.from(atob(event.data), (char) => char.charCodeAt(0));
        instance.write(bytes, done);
      } else if (event.kind === 'gap') {
        instance.reset();
        instance.writeln('\r\n[部分输出已超过内存缓冲，以下从保留位置继续]');
        done();
      } else {
        instance.writeln(`\r\n[进程已退出 · exit ${event.exit_code ?? 'unknown'}]`);
        done();
        void queryClient.invalidateQueries({ queryKey: ['runs'] });
      }
    }).catch((error) => errorRef.current(String(error)));
    return () => {
      disposed = true; input.dispose(); binary.dispose(); observer.disconnect();
      void unsubscribe(run.id).catch(() => undefined);
      instance.dispose(); terminal.current = null; fit.current = null;
    };
  }, [run.id, queryClient]);
  useEffect(() => {
    if (terminal.current) terminal.current.options.theme = terminalThemes[theme];
  }, [theme, run.id, queryClient]);
  useEffect(() => {
    if (visible && host.current?.clientWidth) {
      const id = requestAnimationFrame(() => {
        fit.current?.fit();
        const current = terminal.current;
        if (current) void resizePty(run.id, current.cols, current.rows).catch(() => undefined);
      });
      return () => cancelAnimationFrame(id);
    }
  }, [visible, run.id]);
  return <div className={`pty-host ${visible ? '' : 'hidden'}`} ref={host} aria-label={`${run.agent} 交互终端`} />;
}
