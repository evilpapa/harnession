import { create } from 'zustand';
import type { ViewMode } from './types';

type WorkspaceState = {
  mode: ViewMode; inspector: boolean; project: string | null; agent: string; selected: string | null;
  activeRun: string | null; terminalKind: 'record' | 'live'; focus: boolean;
};
type State = WorkspaceState & {
  patch: (state: Partial<WorkspaceState>) => void;
  selectProject: (project: string | null) => void;
  selectAgent: (agent: string) => void;
};
export const useWorkspace = create<State>((set) => ({
  mode: 'chat', inspector: false, project: null, agent: '', selected: null,
  activeRun: null, terminalKind: 'record', focus: false, patch: (state) => set(state),
  // Sidebar counts are global: changing dimension must clear the previous scope.
  selectProject: (project) => set({ project, agent: '', selected: null }),
  selectAgent: (agent) => set({ project: null, agent, selected: null }),
}));
