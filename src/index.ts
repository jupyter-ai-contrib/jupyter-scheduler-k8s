import React from 'react';

import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { Token } from '@lumino/coreutils';
import K8sAdvancedOptions from './advanced-options';

// Define our own token that matches the jupyter-scheduler IAdvancedOptions token
export const IAdvancedOptions = new Token<React.FC<any>>(
  '@jupyterlab/scheduler:IAdvancedOptions'
);

/**
 * The K8s-specific advanced options plugin for jupyter-scheduler.
 * This replaces the default advanced options with K8s resource configuration.
 */
const k8sAdvancedOptions: JupyterFrontEndPlugin<React.FC<any>> = {
  id: 'jupyter-scheduler-k8s:IAdvancedOptions',
  autoStart: true,
  provides: IAdvancedOptions,
  activate: (app: JupyterFrontEnd) => {
    console.log('Activating jupyter-scheduler-k8s advanced options');
    return K8sAdvancedOptions;
  }
};

const plugins: JupyterFrontEndPlugin<any>[] = [k8sAdvancedOptions];

export default plugins;