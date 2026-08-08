# 14 — Complete Feature Gap Matrix

| Domain | WEBKNOSSOS behavior | WEBKNOSSOS implementation | mito-data-agent behavior | mito-data-agent implementation | Gap | Recommended action |
|---|---|---|---|---|---|---|
| Organizations | Multi-tenant org boundary | `Organization` models/controllers | Institution string/FK weak | `accounts.Institution` | Large | Introduce Org≈Institution hardening or new Organization |
| Teams | Dataset+task scoping | Teams + membership | Project membership informal | `is_project_member` helpers | Large | Add Team model |
| Roles | Admin/TM/User | Silhouette + role flags | manager/annotator/requester | `UserProfile.role` | Medium | Map + add team manager |
| Experience | domain:value gating | `user_experiences` | difficulty/quality_score | AnnotatorProfile | Large | Port experience domains |
| Invitations | Org invites | user admin flows | public register limited | accounts API | Medium | Add invites |
| Dataset permissions | Team-based | dataset ACL | project membership | services ACL | Medium | Formalize |
| Audit logs | time tracking + admin | tracingTime, analytics | review records only | ReviewRecord | Medium | Event/audit log |
| Task types | Reusable blueprints | TaskType + settings | enum only | TaskType choices | Large | New TaskType entity |
| Task instances | N redundant annotations | pendingInstances + triggers | 1 assignee/task | assigned_to FK | Large | Redesign |
| Pull assignment | Request next | Serializable assignNext | Manager push/plan | services.assign* | Large | Implement claim API |
| Auto-fill push | Not first-class | — | plan apply semi-manual | AssignmentPlan* | New capability | Design beyond WK |
| Priorities | Project+task | projects.priority | task priority | PriorityLevel | Medium | Add project priority/pause |
| Concurrency safety | Serializable+triggers | Task.scala / SQL | unsafe multi-worker | SQLite | Critical | Postgres + locks |
| Review loop | Finish + download/compound | annotation states | approve/reject/revise/lock | services.review* | mito lead | Retain & extend |
| Time tracking | Per annotation | tracingTime | timestamps only | assigned_at etc | Medium | Add session timing |
| Progress dashboards | Project progress UI | admin/statistic/* | lifecycle tabs | lifecycle.py | Medium | Enrich |
| Brush/erase | WebGL volume tools | volumetracing | Canvas tools | AnnotationCanvas | UX retain | Keep UI; improve ops |
| Interpolation | SDF slice fill | volume_interpolation_saga | Missing | — | High | Port algorithm |
| Flood fill 2D/3D | Yes | fill tools | Partial AI fills | SAM/watershed | Medium | Add classical fill |
| Quick Select / SAM | Threshold + hosted AI | tools + jobs | EfficientSAM+SAM2 live | vendor models | mito lead | Keep; optional WK AI later |
| Proofreading agglomerate | Graph merge/split | tracingstore | Split/merge volume ops | services | Different | Keep mito ops; optional graph later |
| Undo/redo | Bucket stroke history | sagas | Full-slice snapshots max 20 | AnnotationCanvas | High | Op-log undo |
| Autosave | PushQueue continuous | pushqueue | Manual Save | dirty flag | High | Autosave |
| Deep links | Coord+state URLs | sharing | Hard-case token | HardCase | Medium | Generalize |
| Formats | Zarr/WKW/N5/NG | datastore | TIFF/NIfTI memmap | slice_io | Critical | Pyramids + chunk svc |
| Chunk streaming | PullQueue | frontend+datastore | Per-slice JPEG | Django | Critical | Redesign |
| Rendering | WebGL planes | texture managers | Canvas2D | AnnotationCanvas | High | Hybrid or WebGL layers |
| Meshes | Ad-hoc + precomputed | datastore mesh APIs | marching cubes panel | Labels3DPanel | Medium | Upgrade |
| Inference jobs | Hosted AI jobs | commercial | Scaffold ProcessingJob | processing app | High | Complete Slurm+nnU-Net |
| Slurm | Not core WK | — | Adapter exists unused | adapters/slurm.py | mito lead | Finish integration |
| Monitoring | telemetry/health | airbrake, health checks | logging basic | Django logs | High | Metrics/tracing |
| Tests | FE+BE extensive | vitest/sbt | BE only | manage.py test | High | FE+load+soak |
| Deploy | Docker | compose/Hub | scripts only | dev-launch | High | Prod compose |
| License hygiene | AGPL notices | LICENSE | no LICENSE | — | High | Compliance pack |
