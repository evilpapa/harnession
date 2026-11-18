/** Display home paths without changing the paths used for filtering or commands. */
export function displayPath(value: string | null | undefined, home: string | undefined): string {
  if (!value || !home) return value || '';
  const normalized = value.replace(/\\/g, '/').replace(/\/+$/, '');
  const root = home.replace(/\\/g, '/').replace(/\/+$/, '');
  if (!root) return value;
  const windows = /^[a-z]:\//i.test(root) || home.startsWith('\\\\');
  const candidate = windows ? normalized.toLowerCase() : normalized;
  const prefix = windows ? root.toLowerCase() : root;
  if (candidate === prefix) return '~';
  if (candidate.startsWith(prefix + '/')) return '~' + normalized.slice(root.length);
  return value;
}

export function projectLabel(value: string | null | undefined, home: string | undefined): string {
  const path = displayPath(value, home);
  return path.replace(/[\\/]+$/, '').split(/[\\/]/).at(-1) || '未归类';
}
