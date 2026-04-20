import React from 'react';
import { getPhotoUrl } from '../api';

export default function Avatar({ firstName, lastName, photoFilename, size = 'md' }) {
  const url = getPhotoUrl(photoFilename);
  const initials = `${(firstName || '')[0] || ''}${(lastName || '')[0] || ''}`.toUpperCase();
  const cls = `avatar avatar--${size}`;

  if (url) {
    return (
      <div className={cls}>
        <img src={url} alt={`${firstName} ${lastName}`} />
      </div>
    );
  }

  return <div className={cls}>{initials}</div>;
}
