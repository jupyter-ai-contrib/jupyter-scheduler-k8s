import React, { ChangeEvent } from 'react';

import {
  FormControl,
  FormLabel,
  InputLabel,
  MenuItem,
  Select,
  SelectChangeEvent,
  Stack,
  TextField
} from '@mui/material';

// Local type definitions based on jupyter-scheduler interfaces
enum JobsView {
  CreateForm = 1,
  JobDetail,
  JobDefinitionDetail
}

type ErrorsType = { [key: string]: string };

interface IAdvancedOptionsProps {
  jobsView: JobsView;
  model: any;  // Using any since we can't import the exact types
  handleModelChange: (model: any) => void;
  errors: ErrorsType;
  handleErrorsChange: (errors: ErrorsType) => void;
}

const RESOURCE_PROFILES: Record<string, {label: string, cpu: string, memory: string, gpu: number}> = {
  '': {
    label: 'Default (cluster defaults)',
    cpu: '',
    memory: '',
    gpu: 0
  },
  'cpu-0.5-mem-1': {
    label: '0.5 CPU, 1Gi Memory',
    cpu: '500m',
    memory: '1Gi',
    gpu: 0
  },
  'cpu-1-mem-2': {
    label: '1 CPU, 2Gi Memory',
    cpu: '1',
    memory: '2Gi',
    gpu: 0
  },
  'cpu-2-mem-4': {
    label: '2 CPU, 4Gi Memory',
    cpu: '2',
    memory: '4Gi',
    gpu: 0
  },
  'cpu-4-mem-8': {
    label: '4 CPU, 8Gi Memory',
    cpu: '4',
    memory: '8Gi',
    gpu: 0
  },
  'cpu-8-mem-16': {
    label: '8 CPU, 16Gi Memory',
    cpu: '8',
    memory: '16Gi',
    gpu: 0
  },
  'gpu-1-cpu-2-mem-4': {
    label: '1 GPU, 2 CPU, 4Gi Memory',
    cpu: '2',
    memory: '4Gi',
    gpu: 1
  },
  'gpu-1-cpu-4-mem-8': {
    label: '1 GPU, 4 CPU, 8Gi Memory',
    cpu: '4',
    memory: '8Gi',
    gpu: 1
  },
  'gpu-2-cpu-8-mem-16': {
    label: '2 GPU, 8 CPU, 16Gi Memory',
    cpu: '8',
    memory: '16Gi',
    gpu: 2
  },
  custom: {
    label: 'Custom',
    cpu: '',
    memory: '',
    gpu: 0
  }
};

const K8sAdvancedOptions = (
  props: IAdvancedOptionsProps
): JSX.Element => {
  const formPrefix = 'jp-create-job-k8s-advanced-';

  const handleInputChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (props.jobsView !== JobsView.CreateForm) {
      return;
    }

    props.handleModelChange({
      ...props.model,
      [e.target.name]: e.target.value
    });
  };

  const handleSelectChange = (e: SelectChangeEvent<string>) => {
    if (props.jobsView !== JobsView.CreateForm) {
      return;
    }

    const profile = e.target.value;
    if (profile === '') {
      props.handleModelChange({
        ...props.model,
        k8s_resource_profile: '',
        k8s_cpu: undefined,
        k8s_memory: undefined,
        k8s_gpu: undefined
      });
    } else if (profile !== 'custom') {
      const selectedProfile = RESOURCE_PROFILES[profile];
      props.handleModelChange({
        ...props.model,
        k8s_resource_profile: profile,
        k8s_cpu: selectedProfile.cpu,
        k8s_memory: selectedProfile.memory,
        k8s_gpu: selectedProfile.gpu
      });
    } else {
      props.handleModelChange({
        ...props.model,
        k8s_resource_profile: profile
      });
    }
  };

  const validateResources = (name: string, value: any): string | null => {
    if (name === 'k8s_gpu') {
      const numValue = parseInt(value) || 0;
      if (numValue < 0) {
        return 'GPU count cannot be negative';
      }
    }
    return null;
  };

  const handleCustomInputChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (props.jobsView !== JobsView.CreateForm) {
      return;
    }

    const { name, value } = e.target;
    
    const error = validateResources(name, value);
    const newErrors = { ...props.errors };
    if (error) {
      newErrors[name] = error;
    } else {
      delete newErrors[name];
    }
    props.handleErrorsChange(newErrors);

    props.handleModelChange({
      ...props.model,
      [name]: name === 'k8s_gpu' ? parseInt(value) || 0 : value
    });
  };

  const handleTagChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (props.jobsView !== JobsView.CreateForm) {
      return;
    }

    const { name, value } = event.target;
    const tagIdxMatch = name.match(/^tag-(\d+)$/);

    if (tagIdxMatch === null) {
      return null;
    }

    const newTags = props.model.tags ?? [];
    newTags[parseInt(tagIdxMatch[1])] = value;

    props.handleModelChange({
      ...props.model,
      tags: newTags
    });
  };

  const addTag = () => {
    if (props.jobsView !== JobsView.CreateForm) {
      return;
    }

    const newTags = [...(props.model.tags ?? []), ''];
    props.handleModelChange({
      ...props.model,
      tags: newTags
    });
  };

  const deleteTag = (idx: number) => {
    if (props.jobsView !== JobsView.CreateForm) {
      return;
    }

    const newTags = props.model.tags ?? [];
    newTags.splice(idx, 1);
    props.handleModelChange({
      ...props.model,
      tags: newTags
    });
  };

  const tags = props.model.tags ?? [];

  const createTags = () => {
    return (
      <Stack spacing={2}>
        {tags.map((tag: string, idx: number) => (
          <Stack key={idx} direction="row" spacing={1} alignItems="center">
            <TextField
              label={`Tag ${idx + 1}`}
              id={`${formPrefix}tag-${idx}`}
              name={`tag-${idx}`}
              value={tag}
              onChange={handleTagChange}
              size="small"
            />
            <button
              onClick={() => deleteTag(idx)}
              title={`Delete tag ${idx + 1}`}
              style={{ padding: '4px', border: 'none', background: 'transparent', cursor: 'pointer' }}
            >
              ✕
            </button>
          </Stack>
        ))}
        <button
          onClick={addTag}
          title="Add new tag"
          style={{ padding: '8px 16px', border: '1px solid #ccc', background: 'white', cursor: 'pointer' }}
        >
          + Add Tag
        </button>
      </Stack>
    );
  };

  const showTags = () => {
    if (!props.model.tags) {
      return (
        <Stack spacing={2}>
          <p>
            <em>No tags</em>
          </p>
        </Stack>
      );
    }

    return (
      <Stack spacing={2}>
        {tags.map((tag: string, idx: number) => (
          <div key={idx}>
            <strong>Tag {idx + 1}:</strong> {tag}
          </div>
        ))}
      </Stack>
    );
  };

  const tagsDisplay: JSX.Element | null =
    props.jobsView === JobsView.CreateForm ? createTags() : showTags();

  const model = props.model as any;
  const resourceProfile = model.k8s_resource_profile || '';
  const isCustom = resourceProfile === 'custom';

  const hasResources = resourceProfile || model.k8s_cpu || model.k8s_memory || model.k8s_gpu;
  
  const renderCreateForm = () => (
    <>
      <FormLabel component="legend">Kubernetes Resources (Optional)</FormLabel>
      <Stack spacing={2}>
        <FormControl fullWidth>
          <InputLabel id={`${formPrefix}resource-profile-label`}>
            Resource Profile
          </InputLabel>
          <Select
            labelId={`${formPrefix}resource-profile-label`}
            id={`${formPrefix}resource-profile`}
            name="k8s_resource_profile"
            value={resourceProfile}
            onChange={handleSelectChange}
            label="Resource Profile"
          >
            {Object.entries(RESOURCE_PROFILES).map(([key, profile]) => (
              <MenuItem key={key} value={key}>
                {profile.label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        {isCustom && (
          <>
            <TextField
              label="CPU Cores"
              id={`${formPrefix}cpu`}
              name="k8s_cpu"
              value={model.k8s_cpu || ''}
              onChange={handleCustomInputChange}
              helperText="Examples: 0.5, 1, 2, 500m"
              variant="outlined"
              error={!!props.errors.k8s_cpu}
            />
            <TextField
              label="Memory"
              id={`${formPrefix}memory`}
              name="k8s_memory"
              value={model.k8s_memory || ''}
              onChange={handleCustomInputChange}
              helperText={
                props.errors.k8s_memory || 
                'Examples: 512Mi (512 MB), 1Gi (1 GB), 2Gi (2 GB)'
              }
              variant="outlined"
              error={!!props.errors.k8s_memory}
            />
            <TextField
              label="GPU Count"
              id={`${formPrefix}gpu`}
              name="k8s_gpu"
              type="number"
              value={model.k8s_gpu || 0}
              onChange={handleCustomInputChange}
              helperText={
                props.errors.k8s_gpu || 
                'Number of NVIDIA GPUs (0 for none, max 8)'
              }
              variant="outlined"
              error={!!props.errors.k8s_gpu}
              inputProps={{ min: 0, max: 8, step: 1 }}
            />
          </>
        )}
      </Stack>
    </>
  );

  const renderDetailView = () => {
    if (!hasResources) return null;
    
    return (
      <>
        <FormLabel component="legend">Kubernetes Resources</FormLabel>
        <Stack spacing={2}>
          {resourceProfile && (
            <div>
              <strong>Resource Profile:</strong> {RESOURCE_PROFILES[resourceProfile]?.label || resourceProfile}
            </div>
          )}
          {model.k8s_cpu && (
            <div>
              <strong>CPU:</strong> {model.k8s_cpu}
            </div>
          )}
          {model.k8s_memory && (
            <div>
              <strong>Memory:</strong> {model.k8s_memory}
            </div>
          )}
          {model.k8s_gpu > 0 && (
            <div>
              <strong>GPU:</strong> {model.k8s_gpu}
            </div>
          )}
        </Stack>
      </>
    );
  };

  const resourceConfigSection = props.jobsView === JobsView.CreateForm 
    ? renderCreateForm() 
    : renderDetailView();

  const idemTokenLabel = 'Idempotency token';
  const idemTokenName = 'idempotencyToken';
  const idemTokenId = `${formPrefix}${idemTokenName}`;

  return (
    <Stack spacing={4}>
      {resourceConfigSection}
      {props.jobsView === JobsView.JobDetail && (
        <div>
          <strong>{idemTokenLabel}:</strong> {props.model.idempotencyToken}
        </div>
      )}
      {props.jobsView === JobsView.CreateForm &&
        props.model.createType === 'Job' && (
          <TextField
            label={idemTokenLabel}
            variant="outlined"
            onChange={handleInputChange}
            value={props.model.idempotencyToken}
            id={idemTokenId}
            name={idemTokenName}
          />
        )}

      <FormLabel component="legend">Tags</FormLabel>
      {tagsDisplay}
    </Stack>
  );
};

export default K8sAdvancedOptions;