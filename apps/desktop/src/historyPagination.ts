import type { Page } from './types';

const initialSize = 60;
const requestLimit = 200;
export const loadAllHistoryClick = 9;

export function historyBatchSize(click: number) {
  if (click >= loadAllHistoryClick) return Infinity;
  return initialSize * 2 ** Math.floor(Math.max(0, click - 1) / 3);
}

type HistoryCursor = { cursor: number; generation: string | null; limit: number };

export async function readHistoryBatch<T>(
  { cursor, generation }: Omit<HistoryCursor, 'limit'>,
  size: number,
  readPage: (params: HistoryCursor) => Promise<Page<T>>,
  signal?: AbortSignal,
): Promise<Page<T>> {
  const items: T[] = [];
  const read = async () => {
    signal?.throwIfAborted();
    const page = await readPage({ cursor, generation, limit: Math.min(requestLimit, size - items.length) });
    signal?.throwIfAborted();
    if (page.reset) throw new Error('消息快照已更新，请重新读取。');
    items.push(...page.items);
    generation = page.generation;
    return page;
  };

  const firstPage = await read();
  let nextCursor = firstPage.next_cursor;
  while (nextCursor !== null && items.length < size) {
    cursor = nextCursor;
    const page = await read();
    nextCursor = page.next_cursor;
  }
  return { ...firstPage, items, next_cursor: nextCursor };
}
