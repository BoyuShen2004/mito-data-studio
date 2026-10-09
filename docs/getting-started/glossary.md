# Glossary

| Term | Meaning in this application | Common distinction |
| --- | --- | --- |
| Project | Related research work, members, data and tasks | A project is not an image file |
| Dataset | A group of volumes within a project | One project can contain multiple datasets |
| Volume | Microscopy image data with associated labels, ROI and metadata | A slice is one plane of a volume |
| Voxel | One element of a 3-D array | Voxel count is not physical volume |
| Z / Y / X | Internal array axis order; axial navigation indexes Z | Do not swap axes without checking the data contract |
| Physical voxel size | Physical size of a voxel along each axis | Measurements uses nm in the form; stored metadata uses µm |
| Registered source image | Microscopy intensity data registered with the application | Annotation does not paint into the source image |
| Label / instance ID | Integer segmentation: 0 is background, positive integers identify objects | Display colors do not create scientific categories |
| ROI / region mask | A read-only region of interest; nonzero means inside | It is not editable mitochondria instance segmentation |
| Task | Volume annotation work assigned to one annotator | Project access does not imply task assignment |
| Working label / saved working draft | Editable label persisted by a successful save | Pending browser-only edits are excluded; autosave can persist them |
| Pending edit | A browser-buffer change not yet acknowledged by a successful save | It can be saved explicitly or by best-effort autosave |
| Submission snapshot | Immutable label copy created by Submit for review | Later working-label edits do not update its bytes |
| Official label | Current official reference: initial label, approved snapshot or draft promoted on withdrawal | It need not be the latest draft or an approved result |
| SAM2 proposal | Model-generated candidate segmentation awaiting inspection | It is not automatically accepted ground truth |
| Hard case | A recorded object or question for team discussion | It is not another submission channel |
| Pyramid | Derived multiresolution data for viewing | It replaces neither the source nor the editable draft |
| API | Interface between browser and backend | Hidden buttons do not substitute for backend authorization |
| Migration | Versioned database schema or data change | It is not an image copy or database reset |
| Processing dispatcher | Process that claims and executes queued processing jobs | Successful queueing does not mean execution has started |
| Development / production | Practice/development environment versus live research service | Keep their databases, storage and configuration separate |

See the [data contract](../engineering/data-and-storage.md) for exact axes,
formats and storage conventions.
