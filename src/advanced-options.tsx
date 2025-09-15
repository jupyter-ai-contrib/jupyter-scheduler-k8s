import React, { useState, useEffect, ChangeEvent } from 'react';
import {
  TextField,
  Stack,
  Typography,
  Box,
  Alert,
  IconButton
} from '@mui/material';
import { Scheduler } from '@jupyterlab/scheduler';
import { JobsView } from '@jupyterlab/scheduler';
import { addIcon, closeIcon } from '@jupyterlab/ui-components';

interface IEnvVar {
  name: string;
  value: string;
}

const ENV_VAR_REGEX = /^[A-Za-z_][A-Za-z0-9_]*$/;
const PROTECTED_VARS = [
  'S3_INPUT_PREFIX', 'S3_OUTPUT_PREFIX', 'NOTEBOOK_PATH',
  'OUTPUT_PATH', 'PARAMETERS', 'OUTPUT_FORMATS', 'PACKAGE_INPUT_FOLDER',
  'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
  'AWS_DEFAULT_REGION', 'AWS_REGION', 'S3_ENDPOINT_URL'
];

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
  
  // Convert Dict to array for UI
  const [envVars, setEnvVars] = useState<IEnvVar[]>(() => {
    const envDict = (model as any).environmentVariables || {};
    return Object.entries(envDict).map(([name, value]) => ({
      name,
      value: String(value)
    }));
  });

  useEffect(() => {
    // Convert array to Dict for storage (last value wins for duplicates)
    const envVarsDict: Record<string, string> = {};
    envVars.forEach(env => {
      if (env.name && !PROTECTED_VARS.includes(env.name)) {
        envVarsDict[env.name] = env.value;
      }
    });
    
    const updatedModel = {
      ...model,
      runtimeEnvironmentParameters: {
        ...model.runtimeEnvironmentParameters,
        k8s_cpu: resources.cpu,
        k8s_memory: resources.memory,
        k8s_gpu: resources.gpu
      },
      environmentVariables: envVarsDict
    };

    handleModelChange(updatedModel as any);
  }, [resources, envVars]);

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
    
    // Validate environment variables
    const seenNames = new Set<string>();
    envVars.forEach((envVar, idx) => {
      const nameKey = `env-${idx}-name`;
      const valueKey = `env-${idx}-value`;
      
      if (!envVar.name) {
        newErrors[nameKey] = 'Environment variable name is required';
      } else if (!ENV_VAR_REGEX.test(envVar.name)) {
        newErrors[nameKey] = 'Invalid name: must start with letter or underscore, contain only letters, numbers, underscores';
      } else if (PROTECTED_VARS.includes(envVar.name)) {
        newErrors[nameKey] = `Cannot override protected variable: ${envVar.name}`;
      } else if (seenNames.has(envVar.name)) {
        newErrors[nameKey] = `Duplicate name: ${envVar.name} (last value will be used)`;
      } else {
        delete newErrors[nameKey];
      }
      
      if (envVar.name) {
        seenNames.add(envVar.name);
      }
      
      // Empty values are allowed (common for flags)
      delete newErrors[valueKey];
    });
    
    handleErrorsChange(newErrors);
  }, [resources, envVars]);

  const handleResourceChange = (field: string) => (
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    setResources({
      ...resources,
      [field]: event.target.value
    });
  };
  
  const handleEnvVarChange = (event: ChangeEvent<HTMLInputElement>) => {
    const match = event.target.name.match(/^env-(\d+)-(name|value)$/);
    if (!match) return;
    
    const idx = parseInt(match[1]);
    const field = match[2] as 'name' | 'value';
    
    const newEnvVars = [...envVars];
    newEnvVars[idx] = {
      ...newEnvVars[idx],
      [field]: event.target.value
    };
    setEnvVars(newEnvVars);
  };
  
  const addEnvVar = () => {
    setEnvVars([...envVars, { name: '', value: '' }]);
  };
  
  const removeEnvVar = (idx: number) => {
    const newEnvVars = envVars.filter((_, i) => i !== idx);
    setEnvVars(newEnvVars);
  };

  return (
    <Stack spacing={3}>
      <Box>
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
      </Box>
      
      <Box>
        <Typography variant="h6" component="h3" sx={{ mb: 2 }}>
          Environment Variables
        </Typography>
        
        {isReadOnly ? (
          <Box>
            {envVars.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No environment variables configured
              </Typography>
            ) : (
              envVars.map((envVar, idx) => (
                <Typography key={idx} variant="body2">
                  {envVar.name}: {envVar.value || '(empty)'}
                </Typography>
              ))
            )}
          </Box>
        ) : (
          <Stack spacing={2}>
            {envVars.map((envVar, idx) => {
              const nameError = errors[`env-${idx}-name`];
              return (
                <Box key={idx} display="flex" gap={1} alignItems="flex-start">
                  <TextField
                    name={`env-${idx}-name`}
                    value={envVar.name}
                    onChange={handleEnvVarChange}
                    placeholder="Name"
                    size="small"
                    error={!!nameError}
                    helperText={nameError}
                    sx={{ flexGrow: 1 }}
                  />
                  <TextField
                    name={`env-${idx}-value`}
                    value={envVar.value}
                    onChange={handleEnvVarChange}
                    placeholder="Value"
                    size="small"
                    sx={{ flexGrow: 1 }}
                  />
                  <IconButton
                    onClick={() => removeEnvVar(idx)}
                    title="Remove this environment variable"
                    size="small"
                    sx={{ mt: 0.5 }}
                  >
                    <closeIcon.react />
                  </IconButton>
                </Box>
              );
            })}
            
            <Box>
              <IconButton
                onClick={addEnvVar}
                title="Add environment variable"
                size="small"
              >
                <addIcon.react />
              </IconButton>
            </Box>
            
            {envVars.length > 0 && (
              <Alert severity="info">
                <Typography variant="body2">
                  Environment variables will be available in the notebook execution environment.
                  Protected system variables cannot be overridden.
                </Typography>
              </Alert>
            )}
          </Stack>
        )}
      </Box>
    </Stack>
  );
};

export default K8sAdvancedOptions;