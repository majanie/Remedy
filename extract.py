#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
Created on Mon Mar 11 11:48:55 2019

@author: gregz
"""

import numpy as np

from astropy.convolution import Gaussian2DKernel, convolve
from astropy.modeling.models import Moffat2D, Gaussian2D
from input_utils import setup_logging
from scipy.interpolate import griddata, LinearNDInterpolator

class Extract:
    def __init__(self, wave=None):
        '''
        Initialize Extract class
        
        Parameters
        ----------
        wave: numpy 1d array
            wavelength of calfib extension for hdf5 files, does not need to be
            set unless needed by development team
        '''
        if wave is not None:
            self.wave = wave
        else:
            self.wave = self.get_wave()
        self.get_ADR()
        self.log = setup_logging('Extract')
        self.set_dither_pattern()
    
    def set_dither_pattern(self, dither_pattern=None):
        ''' 
        Set dither pattern (default if None given)
        
        Parameters
        ----------
        dither_pattern: numpy array (length of exposures)
            only necessary if the dither pattern isn't the default
        '''
        if dither_pattern is None:
            self.dither_pattern = np.array([[0., 0.], [1.27, -0.73],
                                            [1.27, 0.73]])
        else:
            self.dither_pattern = dither_pattern

    def get_wave(self):
        ''' Return implicit wavelength solution for calfib extension '''
        return np.linspace(3470, 5540, 1036)

    def get_ADR(self, angle=0.):
        ''' 
        Use default ADR from Karl Gebhardt (adjusted by Greg Zeimann)

        Parameters
        ----------
        angle: float
            angle=0 along x-direction, appropriate if observing at PA
        dither_pattern: numpy array (length of exposures)
            only necessary if the dither pattern isn't the default
        '''
        wADR = [3500., 4000., 4500., 5000., 5500.]
        ADR = [-0.74, -0.4, -0.08, 0.08, 0.20]
        ADR = np.polyval(np.polyfit(wADR, ADR, 3), self.wave)
        self.ADRx = np.cos(np.deg2rad(angle)) * ADR
        self.ADRy = np.sin(np.deg2rad(angle)) * ADR


    def intersection_area(self, d, R, r):
        """
        Return the area of intersection of two circles.
        The circles have radii R and r, and their centers are separated by d.
    
        Parameters
        ----------
        d : float
            separation of the two circles
        R : float
            Radius of the first circle
        r : float
            Radius of the second circle
        """
    
        if d <= abs(R-r):
            # One circle is entirely enclosed in the other.
            return np.pi * min(R, r)**2
        if d >= r + R:
            # The circles don't overlap at all.
            return 0
    
        r2, R2, d2 = r**2, R**2, d**2
        alpha = np.arccos((d2 + r2 - R2) / (2*d*r))
        beta = np.arccos((d2 + R2 - r2) / (2*d*R))
        answer = (r2 * alpha + R2 * beta -
                  0.5 * (r2 * np.sin(2*alpha) + R2 * np.sin(2*beta)))
        return answer

    def tophat_psf(self, radius, boxsize, scale, fibradius=0.75):
        '''
        Tophat PSF profile image 
        (taking fiber overlap with fixed radius into account)
        
        Parameters
        ----------
        radius: float
            Radius of the tophat PSF
        boxsize: float
            Size of image on a side for Moffat profile
        scale: float
            Pixel scale for image
        
        Returns
        -------
        zarray: numpy 3d array
            An array with length 3 for the first axis: PSF image, xgrid, ygrid
        '''
        xl, xh = (0. - boxsize / 2., 0. + boxsize / 2. + scale)
        yl, yh = (0. - boxsize / 2., 0. + boxsize / 2. + scale)
        x, y = (np.arange(xl, xh, scale), np.arange(yl, yh, scale))
        xgrid, ygrid = np.meshgrid(x, y)
        t = np.linspace(0., np.pi * 2., 360)
        r = np.arange(radius - fibradius, radius + fibradius * 7./6.,
                      1. / 6. * fibradius)
        xr, yr, zr = ([], [], [])
        for i, ri in enumerate(r):
            xr.append(np.cos(t) * ri)
            yr.append(np.sin(t) * ri)
            zr.append(self.intersection_area(ri, radius, fibradius) *
                      np.ones(t.shape) / (np.pi * fibradius**2))
        xr, yr, zr = [np.hstack(var) for var in [xr, yr, zr]]
        psf = griddata(np.array([xr, yr]).swapaxes(0,1), zr, (xgrid, ygrid),
                       method='linear')
        psf[np.isnan(psf)]=0.
        zarray = np.array([psf, xgrid, ygrid])
        zarray[0] /= zarray[0].sum()
        return zarray

    def gaussian_psf(self, xstd, ystd, theta, boxsize, scale):
        '''
        Gaussian PSF profile image 
        
        Parameters
        ----------
        xstd: float
            Standard deviation of the Gaussian in x before rotating by theta
        ystd: float
            Standard deviation of the Gaussian in y before rotating by theta
        theta: float
            Rotation angle in radians.
            The rotation angle increases counterclockwise
        boxsize: float
            Size of image on a side for Moffat profile
        scale: float
            Pixel scale for image
        
        Returns
        -------
        zarray: numpy 3d array
            An array with length 3 for the first axis: PSF image, xgrid, ygrid
        '''
        M = Gaussian2D()
        M.x_stddev.value = xstd
        M.y_stddev.value = ystd
        M.theta.value = theta
        xl, xh = (0. - boxsize / 2., 0. + boxsize / 2. + scale)
        yl, yh = (0. - boxsize / 2., 0. + boxsize / 2. + scale)
        x, y = (np.arange(xl, xh, scale), np.arange(yl, yh, scale))
        xgrid, ygrid = np.meshgrid(x, y)
        zarray = np.array([M(xgrid, ygrid), xgrid, ygrid])
        zarray[0] /= zarray[0].sum()
        return zarray

    def moffat_psf(self, seeing, boxsize, scale, alpha=3.5):
        '''
        Moffat PSF profile image
        
        Parameters
        ----------
        seeing: float
            FWHM of the Moffat profile
        boxsize: float
            Size of image on a side for Moffat profile
        scale: float
            Pixel scale for image
        alpha: float
            Power index in Moffat profile function
        
        Returns
        -------
        zarray: numpy 3d array
            An array with length 3 for the first axis: PSF image, xgrid, ygrid
        '''
        M = Moffat2D()
        M.alpha.value = alpha
        M.gamma.value = 0.5 * seeing / np.sqrt(2**(1./ M.alpha.value) - 1.)
        xl, xh = (0. - boxsize / 2., 0. + boxsize / 2. + scale)
        yl, yh = (0. - boxsize / 2., 0. + boxsize / 2. + scale)
        x, y = (np.arange(xl, xh, scale), np.arange(yl, yh, scale))
        xgrid, ygrid = np.meshgrid(x, y)
        zarray = np.array([M(xgrid, ygrid), xgrid, ygrid])
        zarray[0] /= zarray[0].sum()
        return zarray

    def make_collapsed_image(self, xc, yc, xloc, yloc, data, mask,
                             scale=0.25, seeing_fac=1.8, boxsize=4.,
                             wrange=[3470, 5540], nchunks=11,
                             convolve_image=False,
                             interp_kind='linear'):
        ''' 
        Collapse spectra to make a signle image on a rectified grid.  This
        may be done for a wavelength range and using a number of chunks
        of wavelength to take ADR into account.
        
        Parameters
        ----------
        xc: float
            The ifu x-coordinate for the center of the collapse frame
        yc: float 
            The ifu y-coordinate for the center of the collapse frame
        xloc: numpy array
            The ifu x-coordinate for each fiber
        yloc: numpy array
            The ifu y-coordinate for each fiber
        data: numpy 2d array
            The calibrated spectra for each fiber
        mask: numpy 2d array
            The good fiber wavelengths to be used in collapsed frame
        scale: float
            Pixel scale for output collapsed image
        seeing_fac: float
            seeing_fac = 2.35 * radius of the Gaussian kernel used 
            if convolving the images to smooth out features. Unit: arcseconds
        boxsize: float
            Length of the side in arcseconds for the convolved image
        wrange: list
            The wavelength range to use for collapsing the frame
        nchunks: int
            Number of chunks used to take ADR into account when collapsing
            the fibers.  Use a larger number for a larger wavelength.
            A small wavelength may only need one chunk
        convolve_image: bool
            If true, the collapsed frame is smoothed at the seeing_fac scale
        interp_kind: str
            Kind of interpolation to pixelated grid from fiber intensity
        
        Returns
        -------
        zarray: numpy 3d array
            An array with length 3 for the first axis: PSF image, xgrid, ygrid
        '''
        a, b = data.shape
        N = int(boxsize/scale)
        xl, xh = (xc - boxsize / 2., xc + boxsize / 2.)
        yl, yh = (yc - boxsize / 2., yc + boxsize / 2.)
        x, y = (np.linspace(xl, xh, N), np.linspace(yl, yh, N))
        xgrid, ygrid = np.meshgrid(x, y)
        S = np.zeros((a, 2))
        area = np.pi * 0.75**2
        sel = (self.wave > wrange[0]) * (self.wave <= wrange[1])
        I = np.arange(b)
        ichunk = [np.mean(xi) for xi in np.array_split(I[sel], nchunks)]
        ichunk = np.array(ichunk, dtype=int)
        cnt = 0
        image_list = []
        if convolve_image:
            seeing = seeing_fac / scale
            G = Gaussian2DKernel(seeing / 2.35)
        if interp_kind not in ['linear', 'cubic']:
            self.log.warning('interp_kind must be "linear" or "cubic"')
            self.log.warning('Using "linear" for interp_kind')
            interp_kind='linear'
        
        for chunk, mchunk in zip(np.array_split(data[:, sel], nchunks, axis=1),
                                np.array_split(mask[:, sel], nchunks, axis=1)):
            marray = np.ma.array(chunk, mask=mchunk<1e-8)
            image = np.ma.median(marray, axis=1)
            image = image / np.ma.sum(image)
            S[:, 0] = xloc - self.ADRx[ichunk[cnt]]
            S[:, 1] = yloc - self.ADRy[ichunk[cnt]]
            cnt += 1
            grid_z = (griddata(S[~image.mask], image.data[~image.mask],
                               (xgrid, ygrid), method=interp_kind) *
                      scale**2 / area)
            if convolve_image:
                grid_z = convolve(grid_z, G)
            image_list.append(grid_z)
        image = np.median(image_list, axis=0)
        image[np.isnan(image)] = 0.0
        zarray = np.array([image, xgrid-xc, ygrid-yc])
        return zarray

    def get_psf_curve_of_growth(self, psf):
        '''
        Analyse the curve of growth for an input psf
        
        Parameters
        ----------
        psf: numpy 3d array
            zeroth dimension: psf image, xgrid, ygrid
        
        Returns
        -------
        r: numpy array
            radius in arcseconds
        cog: numpy array
            curve of growth for psf
        '''
        r = np.sqrt(psf[1]**2 + psf[2]**2).ravel()
        inds = np.argsort(r)
        maxr = psf[1].max()
        sel = r[inds] <= maxr
        cog = (np.cumsum(psf[0].ravel()[inds][sel]) /
               np.sum(psf[0].ravel()[inds][sel]))
        return r[inds][sel], cog
    
    def build_weights(self, xc, yc, ifux, ifuy, psf):
        '''
        Build weight matrix for spectral extraction
        
        Parameters
        ----------
        xc: float
            The ifu x-coordinate for the center of the collapse frame
        yc: float 
            The ifu y-coordinate for the center of the collapse frame
        xloc: numpy array
            The ifu x-coordinate for each fiber
        yloc: numpy array
            The ifu y-coordinate for each fiber
        psf: numpy 3d array
            zeroth dimension: psf image, xgrid, ygrid
        
        Returns
        -------
        weights: numpy 2d array (len of fibers by wavelength dimension)
            Weights for each fiber as function of wavelength for extraction
        '''
        S = np.zeros((len(ifux), 2))
        T = np.array([psf[1].ravel(), psf[2].ravel()]).swapaxes(0, 1)
        I = LinearNDInterpolator(T, psf[0].ravel(),
                                 fill_value=0.0)
        weights = np.zeros((len(ifux), len(self.wave)))
        scale = np.abs(psf[1][0, 1] - psf[1][0, 0])
        area = 0.75**2 * np.pi
        for i in np.arange(len(self.wave)):
            S[:, 0] = ifux - self.ADRx[i] - xc
            S[:, 1] = ifuy - self.ADRy[i] - yc
            weights[:, i] = I(S[:, 0], S[:, 1]) * area / scale**2

        return weights

    def get_spectrum(self, data, error, mask, weights):
        '''
        Weighted spectral extraction
        
        Parameters
        ----------
        data: numpy 2d array (number of fibers by wavelength dimension)
            Flux calibrated spectra for fibers within a given radius of the 
            source.  The radius is defined in get_fiberinfo_for_coord().
        error: numpy 2d array
            Error of the flux calibrated spectra 
        mask: numpy 2d array (bool)
            Mask of good wavelength regions for each fiber
        weights: numpy 2d array (float)
            Normalized extraction model for each fiber
        
        Returns
        -------
        spectrum: numpy 1d array
            Flux calibrated extracted spectrum
        spectrum_error: numpy 1d array
            Error for the flux calibrated extracted spectrum
        '''
        spectrum = (np.sum(data * mask * weights, axis=0) /
                    np.sum(mask * weights**2, axis=0))
        spectrum_error = (np.sqrt(np.sum(error**2 * mask * weights, axis=0)) /
                          np.sum(mask * weights**2, axis=0))
        # Only use wavelengths with enough weight to avoid large noise spikes
        w = np.sum(mask * weights**2, axis=0)
        sel = w < np.median(w)*0.1
        spectrum[sel] = np.nan
        spectrum_error[sel] = np.nan
        
        return spectrum, spectrum_error
