import type { Page } from "@playwright/test";
// Synthetic, isolated API responses exercise the real application routes and canvas.
// No server, database, scientific dataset, or authentication behavior is changed.
export const visualTask = {
  id: 42,
  project: 3,
  project_title: 'Cerebellum mitochondria',
  dataset: 'P10 — serial EM',
  volume: 9,
  volume_name: 'cerebellum_P10_block_03',
  z_start: 0,
  z_end: 64,
  status: 'in_progress',
  task_type: 'manual_annotation',
  priority: 4,
  difficulty: 3,
  deadline: null,
  instructions: 'Trace mitochondrial boundaries. Record ambiguous membrane contacts as hard cases.',
  label_type: 'partial',
  assigned_to: 7,
  assigned_to_username: 'researcher',
  can_annotate: true,
  can_submit: true,
  annotation_locked: false,
  submission_count: 0,
  annotation_time: { tracked: false, seconds: null, display: '-' },
  created_at: '2026-09-29T08:00:00Z',
  assigned_at: '2026-09-30T09:00:00Z',
  submitted_at: null,
  approved_at: null,
  last_decision: '',
  review_history: [],
  shape_z: 64,
  shape_y: 256,
  shape_x: 256
};
export const visualVolume = {
  id: 9,
  project: 3,
  dataset: 1,
  dataset_name: visualTask.dataset,
  name: visualTask.volume_name,
  shape_z: 64,
  shape_y: 256,
  shape_x: 256,
  voxel_size_z: null,
  voxel_size_y: null,
  voxel_size_x: null,
  has_label: true,
  has_region_mask: false,
  label_type: 'partial',
  file_format: 'TIFF',
  status: 'ready',
  image_location: 'cerebellum_P10_block_03.tif',
  label_location: 'labels.tif',
  metadata: {},
  streaming_status: 'not_built',
  region_streaming_status: 'absent'
};
export const visualProject = {
  id: 3,
  title: visualTask.project_title,
  dataset: visualTask.dataset,
  datasets: [{
    id: 1,
    name: visualTask.dataset,
    project: 3,
    metadata: { organism: 'Mouse', tissue: 'Cerebellum', imaging_modality: 'Electron microscopy' },
    description: 'Serial electron microscopy annotation'
  }],
  dataset_count: 1,
  volume_count: 1,
  task_count: 2,
  description: 'Mitochondrial instance segmentation and proofreading.',
  metadata: {},
  annotation_target: 'Mitochondria',
  annotation_type: 'instance',
  workflow_type: 'annotation',
  lifecycle: 'active',
  status: 'in_annotation',
  priority: 4,
  paused: false,
  teams: [],
  working_team: null,
  deadline: null,
  created_by: 7,
  created_by_username: 'researcher',
  manager_reviewed: true,
  institution_name: 'Microscopy laboratory'
};
export async function installScientificFixture(page: Page, role = "manager") {
  const writes: unknown[] = [];
  await page.route(url => url.pathname.startsWith('/api/'), async (route) => {
    const u = new URL(route.request().url());
    let data: unknown = [];
    const p = u.pathname;
    if(p.endsWith('/me/'))
      data = {
        id: 7,
        username: 'researcher',
        role,
        institution_name: 'Microscopy laboratory'
      };
    else if(p.includes('/mock-login/'))
      data = { enabled: true, accounts: [{ username: 'demo_annotator', password: 'demo12345', role: 'annotator' }] };
    else if(p.includes('/release/'))
      data = { release: '1.1.5' };
    else if(p.includes('/identity/'))
      data = { features: { FEATURE_ANNOTATION_OPS: true, FEATURE_INTERPOLATION: true, FEATURE_ANNOTATION_TOOLS: true }, service: {}, git: {} };
    else if(p === '/api/tasks/42/')
      data = visualTask;
    else if(p.includes('/my-tasks/') || p === '/api/projects/3/tasks/')
      data = [visualTask, {
        ...visualTask,
        id: 43,
        volume_name: 'cerebellum_P10_block_04_long_acquisition_name',
        status: 'submitted',
        submitted_at: '2026-10-01T09:00:00Z'
      }];
    else if(p === '/api/projects/')
      data = [visualProject];
    else if(p.includes('/summary/') && p.includes('/projects/'))
      data = {
        project: visualProject, progress: {
          volumes: 1,
          total_tasks: 2,
          approved_tasks: 0,
          percent_complete: 0,
          status_counts: {}
        }, workload: []
      };
    else if(p === '/api/projects/3/volumes/')
      data = [visualVolume];
    else if(p === '/api/volumes/9/')
      data = visualVolume;
    else if(p.includes('/entity/'))
      data = { active: false, aggregate_state: 'none', shares: [] };
    else if(p.includes('/meta/'))
      data = {
        shape: { z: 64, y: 256, x: 256 },
        dtype: 'uint8',
        axes: ['z', 'y', 'x'],
        has_label: true,
        has_region_mask: false,
        volume_id: 9,
        ready_streaming: false,
        display_range: { lo: 0, hi: 255 }
      };
    else if(p.includes('/slice/'))
      return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><defs><filter id="n"><feTurbulence baseFrequency=".13" numOctaves="3" seed="8"/><feColorMatrix type="saturate" values="0"/></filter></defs><rect width="256" height="256" fill="#888"/><rect width="256" height="256" filter="url(#n)" opacity=".65"/></svg>' });
    else if(p.includes('/label-state/'))
      data = { max_label_id: 0, next_label_id: 1, revision: '1' };
    else if(p.includes('/label-ids/') && route.request().method() === 'PUT') {
      writes.push(route.request().postDataJSON());
      data = { max_label_id: 1, next_label_id: 2, revision: '1' };
    }
    else if(p.includes('/submit-inapp/')) {
      data = { id: 501, task_detail: { ...visualTask, status: 'submitted', submission_count: 1 } };
    }
    else if(p.includes('/label-ids/')) {
      const axis = u.searchParams.get('axis') || 'z';
      const h = axis === 'z' ? 256 : 64;
      data = { shape: [h, 256], runs: [[0, h * 256]], revision: '1' };
    }
    else if(p.includes('/labels-summary/'))
      data = {
        labels: [], stats: {
          total: 0,
          proposed: 0,
          edited: 0,
          verified: 0
        }
      };
    else if(p.includes('/region-label-ids/'))
      data = { has_region: false, ids: [] };
    else if(p.includes('/track/prompts/'))
      data = { items: [], revision: '1' };
    else if(p.includes('/submissions/'))
      data = [];
    else if(p.includes('/statistics/'))
      data = {};
    else if(p.includes('/warm'))
      data = { warmed: true };
    else if(p.includes('/time'))
      data = {};
    else if(p.includes('/public-shares/tree/'))
      data = { projects: [] };
    return route.fulfill({ json: data });
  });
  return { writes };
}
