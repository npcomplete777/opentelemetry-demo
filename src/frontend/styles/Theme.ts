// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import { DefaultTheme } from 'styled-components';

const Theme: DefaultTheme = {
  colors: {
    otelBlue: '#18181b',
    otelYellow: '#f59e0b',
    otelGray: '#3f3f46',
    otelRed: '#ef4444',
    backgroundGray: 'rgba(244, 244, 245, 0.8)',
    lightBorderGray: 'rgba(63, 63, 70, 0.15)',
    borderGray: '#d4d4d8',
    textGray: '#18181b',
    textLightGray: '#71717a',
    white: '#FFFFFF',
  },
  breakpoints: {
    desktop: '@media (min-width: 768px)',
  },
  sizes: {
    mxLarge: '24px',
    mLarge: '20px',
    mMedium: '14px',
    mSmall: '12px',
    dxLarge: '48px',
    dLarge: '36px',
    dMedium: '16px',
    dSmall: '14px',
    nano: '8px',
  },
  fonts: {
    bold: '900',
    regular: '400',
    semiBold: '600',
    light: '300',
  },
};

export default Theme;
