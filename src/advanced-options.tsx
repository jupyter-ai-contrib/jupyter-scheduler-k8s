import React, { useState, useEffect } from 'react';
import {
  TextField,
  Stack,
  Typography,
  Box,
  Alert
} from '@mui/material';
import { Scheduler } from '@jupyterlab/scheduler';
import { JobsView } from '@jupyterlab/scheduler';

const K8sAdvancedOptions = (
  props: Scheduler.IAdvancedOptionsProps
): JSX.Element => {
  const { model, handleModelChange, jobsView, errors, handleErrorsChange } = props;
  const isReadOnly = jobsView !== JobsView.CreateForm;
  
  const runtimeParams = model.runtimeEnvironmentParameters || {};
  const [resources, setResources] = useState({
    cpu: (runtimeParams.k8s_cpu as string) || '',
    memory: (runtimeParams.k8s_memory as string) || '',
    gpu: (runtimeParams.k8s_gpu as string) || ''
  });

  useEffect(() => {
    const updatedModel = {
      ...model,
      runtimeEnvironmentParameters: {
        ...model.runtimeEnvironmentParameters,
        k8s_cpu: resources.cpu,
        k8s_memory: resources.memory,
        k8s_gpu: resources.gpu
      }
    };
    
    if (jobsView === JobsView.CreateForm) {
      handleModelChange(updatedModel as any);
    } else if (jobsView === JobsView.JobDetail) {
      handleModelChange(updatedModel as any);
    } else if (jobsView === JobsView.JobDefinitionDetail) {
      handleModelChange(updatedModel as any);
    }
  }, [resources]);

  useEffect(() => {
    const newErrors = { ...errors };
    
    if (resources.cpu && !/^\d*\.?\d+[m]?$/.test(resources.cpu)) {
      newErrors.k8s_cpu = 'CPU format: number with optional "m" suffix (e.g., 0.5, 2, 500m)';
    } else if (resources.cpu && parseFloat(resources.cpu) <= 0) {
      newErrors.k8s_cpu = 'CPU must be greater than 0';
    } else {
      delete newErrors.k8s_cpu;
    }
    
    if (resources.memory && !/^\d+(\.\d+)?([KMGT]i?)?$/i.test(resources.memory)) {
      newErrors.k8s_memory = 'Memory format: number with optional unit (e.g., 512Mi, 2Gi, 1024)';
    } else {
      delete newErrors.k8s_memory;
    }
    
    if (resources.gpu) {
      if (!/^\d+$/.test(resources.gpu)) {
        newErrors.k8s_gpu = 'GPU must be a whole number';
      } else if (parseInt(resources.gpu) < 0) {
        newErrors.k8s_gpu = 'GPU cannot be negative';
      } else {
        delete newErrors.k8s_gpu;
      }
    } else {
      delete newErrors.k8s_gpu;
    }
    
    handleErrorsChange(newErrors);
  }, [resources]);

  const handleResourceChange = (field: string) => (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    setResources({
      ...resources,
      [field]: event.target.value
    });
  };

  return (
    <Stack spacing={3}>
      <Typography variant="h6" component="h3">
        Kubernetes Resources
      </Typography>
      
      {isReadOnly ? (
        <Box>
          <Typography variant="body2">
            CPU: {resources.cpu || 'Cluster default'}
          </Typography>
          <Typography variant="body2">
            Memory: {resources.memory || 'Cluster default'}
          </Typography>
          {resources.gpu && (
            <Typography variant="body2">
              GPU: {resources.gpu}
            </Typography>
          )}
        </Box>
      ) : (
        <>
          <Stack spacing={2}>
            <TextField
              label="CPU"
              value={resources.cpu}
              onChange={handleResourceChange('cpu')}
              error={!!errors.k8s_cpu}
              helperText={errors.k8s_cpu || 'CPU cores or millicores (e.g., 0.5, 2, 500m). Leave empty for cluster default.'}
              size="small"
              fullWidth
              placeholder="2"
            />
            
            <TextField
              label="Memory"
              value={resources.memory}
              onChange={handleResourceChange('memory')}
              error={!!errors.k8s_memory}
              helperText={errors.k8s_memory || 'Memory with unit (e.g., 512Mi, 2Gi). Leave empty for cluster default.'}
              size="small"
              fullWidth
              placeholder="4Gi"
            />
            
            <TextField
              label="GPU"
              value={resources.gpu}
              onChange={handleResourceChange('gpu')}
              error={!!errors.k8s_gpu}
              helperText={errors.k8s_gpu || 'Number of GPUs. Leave empty for none.'}
              size="small"
              fullWidth
              placeholder=""
            />
          </Stack>

          <Alert severity="info">
            <Typography variant="body2">
              Optional resource requirements. Empty fields use cluster defaults.
            </Typography>
          </Alert>
        </>
      )}
    </Stack>
  );
};

export default K8sAdvancedOptions;