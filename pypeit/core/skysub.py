""" Module for sky subtraction
"""
from __future__ import (print_function, absolute_import, division, unicode_literals)

import numpy as np
import sys, os

from pypeit import msgs, utils, processimages, ginga
from pypeit.core import pixels, extract, pydl
from matplotlib import pyplot as plt

from scipy.special import ndtr
import scipy



def global_skysub(image, ivar, tilts, thismask, slit_left, slit_righ, inmask = None, bsp=0.6, sigrej=3.0, maxiter=35,
                  trim_edg=(3,3), pos_mask=True, show_fit=False, no_poly=False, npoly=None):
    """
    Perform global sky subtraction on an input slit

    Parameters
    ----------
    image: float ndarray, shape (nspec, nspat)
          Frame to be sky subtracted

    ivar: float ndarray, shape (nspec, nspat)
          Inverse variance image

    tilts: float ndarray, shape (nspec, nspat)
          Tilgs indicating how wavelengths move across the slit

    thismask : numpy boolean array, shape (nspec, nspat)
      Specifies pixels in the slit in question

    slit_left: ndarray of shape (nspec, 1) or (nspec)
      Left slit boundary in floating point pixels.

    slit_righ: ndarray of shape (nspec, 1) or (nspec)
      Right slit boundary in floating point pixels.


    Optional Parameters
    --------------------

    inmask: boolean ndarray, shape (nspec, nspat), default inmask = None
      Input mask for pixels not to be included in sky subtraction fits. True = Good (not masked), False = Bad (masked)

    bsp: float, default bsp = 0.6
      break point spacing in pixel units

    sigrej : float, default sigrej = 3.0
      sigma rejection threshold

    no_poly: bool, optional
      Do not incldue polynomial basis

    trim_edg: tuple of floats  (left_edge, right_edge), default (3,3)
      indicates how many pixels to trim from left and right slit edges for creating the edgemask. These pixels are
      excluded from sky subtraction fits.

    pos_mask: boolean, defualt pos_mask = True
      First do a prelimnary fit to the log of the sky (i.e. positive pixels only). Then use this fit to create an input
      mask from the residuals lmask = (res < 5.0) & (res > -4.0) for the full fit.
      NOTE: pos_mask should be False for near-IR sky residual subtraction, since fitting the log(sky) requires that the
      counts are positive which will not be the case for i.e. an A-B image. Thus the routine will fail if pos_mask is not
      set to False.

    show_fit: boolean, default show_fit = False
       Plot a fit of the sky pixels and model fit to the screen. This feature will block further execution until the screen is closed.

    Returns
    -------
    bgframe : ndarray
      Returns the model sky background at the pixels where thismask is True.

     >>>  skyframe = np.zeros_like(image)
     >>>  thismask = slitpix == thisslit
     >>>  skyframe[thismask] = global_skysub(image,ivar, tilts, thismask, slit_left, slit_righ)

    """

    # Synthesize ximg, and edgmask  from slit boundaries. Doing this outside this
    # routine would save time. But this is pretty fast, so we just do it here to make the interface simpler.

    # TESTING!!!!
    #no_poly=True
    #show_fit=True

    ximg, edgmask = pixels.ximg_and_edgemask(slit_left, slit_righ, thismask, trim_edg=trim_edg)


    # Init
    (nspec, nspat) = image.shape
    piximg = tilts * (nspec-1)
    if inmask is None:
        inmask = np.copy(thismask)


    # Sky pixels for fitting
    inmask_in = (thismask == True) & (ivar > 0.0) & (inmask == True) & (edgmask == False)
    isrt = np.argsort(piximg[thismask])
    pix = piximg[thismask][isrt]
    sky = image[thismask][isrt]
    sky_ivar = ivar[thismask][isrt]
    ximg_fit = ximg[thismask][isrt]
    inmask_fit = inmask_in[thismask][isrt]
    #spatial = spatial_img[fit_sky][isrt]

    # Restrict fit to positive pixels only and mask out large outliers via a pre-fit to the log.
    if (pos_mask is True):
        pos_sky = (sky > 1.0) & (sky_ivar > 0.0)
        if np.sum(pos_sky) > nspec:
            lsky = np.log(sky[pos_sky])
            lsky_ivar = inmask_fit[pos_sky].astype(float)/3.0** 2  # set errors to just be 3.0 in the log
            #lsky_ivar = np.full(lsky.shape, 0.1)
            # Init bspline to get the sky breakpoints (kludgy)
            #tmp = pydl.bspline(wsky[pos_sky], nord=4, bkspace=bsp)
            lskyset, outmask, lsky_fit, red_chi, exit_status = \
                utils.bspline_profile(pix[pos_sky], lsky, lsky_ivar,
                np.ones_like(lsky),inmask = inmask_fit[pos_sky],
                upper=sigrej, lower=sigrej,
                kwargs_bspline={'bkspace':bsp},kwargs_reject={'groupbadpix': True, 'maxrej': 10})
            res = (sky[pos_sky] - np.exp(lsky_fit)) * np.sqrt(sky_ivar[pos_sky])
            lmask = (res < 5.0) & (res > -4.0)
            sky_ivar[pos_sky] = sky_ivar[pos_sky] * lmask
            inmask_fit[pos_sky]=(sky_ivar[pos_sky] > 0.0) & lmask

    # Include a polynomial basis?
    if no_poly:
        poly_basis = np.ones_like(sky)
        npoly = 1
    else:
        npercol = np.fmax(np.floor(np.sum(thismask) / nspec), 1.0)
        # Demand at least 10 pixels per row (on average) per degree of the polynomial
        if npoly is None:
            #npoly_in = 7
            #npoly = np.fmax(np.fmin(npoly_in, (np.ceil(npercol / 10.)).astype(int)), 1)
            if npercol > 100:
                npoly = 3
            elif npercol > 40:
                npoly = 2
            else:
                npoly = 1
        poly_basis = pydl.flegendre(2.0*ximg_fit - 1.0, npoly).T

    # Full fit now
    #full_bspline = pydl.bspline(wsky, nord=4, bkspace=bsp, npoly = npoly)
    #skyset, outmask, yfit, _ = utils.bspline_profile(wsky, sky, sky_ivar, poly_basis,
    #                                                   fullbkpt=full_bspline.breakpoints,upper=sigrej, lower=sigrej,
    #                                                   kwargs_reject={'groupbadpix':True, 'maxrej': 10})


    # Perform the full fit now
    skyset, outmask, yfit, _, exit_status = utils.bspline_profile(pix, sky, sky_ivar,poly_basis,inmask = inmask_fit,
                                                                  nord=4,upper=sigrej, lower=sigrej,
                                                                  maxiter=maxiter,
                                                                  kwargs_bspline = {'bkspace':bsp},
                                                                  kwargs_reject={'groupbadpix':True, 'maxrej': 10})
    # TODO JFH This is a hack for now to deal with bad fits for which iterations do not converge. This is related
    # to the groupbadpix behavior requested for the djs_reject rejection. It would be good to
    # better understand what this functionality is doing, but it makes the rejection much more quickly approach a small
    # chi^2
    if exit_status == 1:
        msgs.warn('Maximum iterations reached in bspline_profile global sky-subtraction for npoly={:d}.'.format(npoly) +
                  msgs.newline() +
                  'Redoing sky-subtraction without polynomial degrees of freedom')
        poly_basis = np.ones_like(sky)
        # Perform the full fit now
        skyset, outmask, yfit, _, exit_status = utils.bspline_profile(pix, sky, sky_ivar, poly_basis, inmask=inmask_fit,
                                                                      nord=4, upper=sigrej, lower=sigrej,
                                                                      maxiter=maxiter,
                                                                      kwargs_bspline={'bkspace': bsp},
                                                                      kwargs_reject={'groupbadpix': False, 'maxrej': 10})

    sky_frame = np.zeros_like(image)
    ythis = np.zeros_like(yfit)
    ythis[isrt] = yfit
    sky_frame[thismask] = ythis

    #skyset.funcname ='legendre'
    #skyset.xmin = spat_min
    #skyset.xmax = spat_max

    # Evaluate and save
    #bgframe, _ = skyset.value(piximg[thismask],x2=spatial_img[thismask])

    # Debugging/checking

    # ToDo This QA ceases to make sense I think for 2-d fits. I need to think about what the best QA would be here, but I think
    # probably looking at residuals as a function of spectral and spatial position like in the flat fielding code.
    if show_fit:
        goodbk = skyset.mask
        # This is approximate
        yfit_bkpt = np.interp(skyset.breakpoints[goodbk], pix,yfit)
        plt.clf()
        ax = plt.gca()
        was_fit_and_masked = inmask_fit & ~outmask
        ax.plot(pix[inmask_fit], sky[inmask_fit], color='k', marker='o', markersize=0.4, mfc='k', fillstyle='full', linestyle='None')
        ax.plot(pix[was_fit_and_masked], sky[was_fit_and_masked], color='red', marker='+', markersize=1.5, mfc='red', fillstyle='full', linestyle='None')
        ax.plot(pix, yfit, color='cornflowerblue')
        ax.plot(skyset.breakpoints[goodbk], yfit_bkpt, color='lawngreen', marker='o', markersize=4.0, mfc='lawngreen', fillstyle='full', linestyle='None')
        ax.set_ylim((0.99*yfit.min(),1.01*yfit.max()))
        plt.show()

    # Return
    # ToDO worth thinking about whether we want to return a mask here. It makese no sense to return outmask
    # in its present form though since that does not refer to the whole image.
    # return bgframe, outmask
    return ythis



# Utility routine used by local_bg_subtraction_slit
def skyoptimal(wave,data,ivar, oprof, sortpix, sigrej = 3.0, npoly = 1, spatial = None, fullbkpt = None):


    nx = data.size
    nc = oprof.shape[0]
    nobj = int(oprof.size / nc)
    if nc != nx:
        raise ValueError('Object profile should have oprof.shape[0] equal to nx')

    msgs.info('Iter     Chi^2     Rejected Pts')
    xmin = 0.0
    xmax = 1.0

    if ((npoly == 1) | (spatial is None)):
        profile_basis = np.column_stack((oprof, np.ones(nx)))
    else:
        xmin = spatial.min()
        xmax = spatial.max()
        x2 = 2.0 * (spatial - xmin) / (xmax - xmin) - 1
        poly_basis = pydl.flegendre(x2, npoly).T
        profile_basis = np.column_stack((oprof, poly_basis))

    relative_mask = (np.sum(oprof, axis=1) > 1e-10)

    indx, = np.where(ivar[sortpix] > 0.0)
    ngood = indx.size
    good = sortpix[indx]
    good = good[wave[good].argsort()]
    relative, = np.where(relative_mask[good])

    outmask = np.zeros(wave.shape, dtype=bool)

    if ngood > 0:
        sset1, outmask_good1, yfit1, red_chi1, exit_status = utils.bspline_profile(
            wave[good], data[good], ivar[good],
            profile_basis[good, :],fullbkpt=fullbkpt, upper=sigrej, lower=sigrej,
            relative=relative,kwargs_reject={'groupbadpix': True, 'maxrej': 5})
    else:
        msgs.warn('All pixels are masked in skyoptimal. Not performing local sky subtraction.')
        return np.zeros_like(wave), np.zeros_like(wave), outmask

    chi2 = (data[good] - yfit1) ** 2 * ivar[good]
    chi2_srt = np.sort(chi2)
    gauss_prob = 1.0 - 2.0 * ndtr(-1.2 * sigrej)
    sigind = int(np.fmin(np.rint(gauss_prob * float(ngood)), ngood - 1))
    chi2_sigrej = chi2_srt[sigind]
    mask1 = (chi2 < chi2_sigrej)
    msgs.info('2nd round....')
    msgs.info('Iter     Chi^2     Rejected Pts')

    if np.any(mask1):
        sset, outmask_good, yfit, red_chi, exit_status = \
            utils.bspline_profile(wave[good], data[good], ivar[good], profile_basis[good, :], inmask=mask1,
                                  fullbkpt=fullbkpt, upper=sigrej, lower=sigrej, relative=relative,
                                  kwargs_reject={'groupbadpix': True, 'maxrej': 1})
    else:
        msgs.warn('All pixels are masked in skyoptimal after first round of rejection. Not performing local sky subtraction.')
        return np.zeros_like(wave), np.zeros_like(wave), outmask

    ncoeff = npoly + nobj
    skyset = pydl.bspline(None, fullbkpt=sset.breakpoints, nord=sset.nord, npoly=npoly)
    # Set coefficients for the sky.
    # The rehshape below deals with the different sizes of the coeff for npoly = 1 vs npoly > 1
    # and mirrors similar logic in the bspline.py
    skyset.coeff = sset.coeff[nobj:, :].reshape(skyset.coeff.shape)

    skyset.mask = sset.mask
    skyset.xmin = xmin
    skyset.xmax = xmax

    sky_bmodel, _ = skyset.value(wave, x2=spatial)

    obj_bmodel = np.zeros(sky_bmodel.shape)
    objset = pydl.bspline(None, fullbkpt=sset.breakpoints, nord=sset.nord)
    objset.mask = sset.mask
    for i in range(nobj):
        objset.coeff = sset.coeff[i, :]
        obj_bmodel1, _ = objset.value(wave)
        obj_bmodel = obj_bmodel + obj_bmodel1 * profile_basis[:, i]

    outmask[good] = outmask_good

    return sky_bmodel, obj_bmodel, outmask


def optimal_bkpts(bkpts_optimal, bsp_min, piximg, sampmask, samp_frac=0.80,
                  skyimage = None, min_spat=None, max_spat=None, debug=False):
    """

    Args:
        bsp_min: float
           Desired B-spline breakpoint spacing in pixels
        piximg: ndarray float, shape = (nspec, nspat)
           Image containing the pixel sampling, i.e. (nspec-1)*tilts
        sampmask: ndarray, bool
           Boolean array indicating the pixels for which the B-spline fit will actually be evaluated. True = Good, False=Bad
    Optional Args:
        samp_frac: float, default = 0.8
           The fraction of spectral direction pixels required to have a sampling difference < bsp_min in order to instead
           adopt a uniform break point spacing, rather adopting the optimally spaced breakpoints.
        skyimage: ndarray, shape = (nspec, nspat), default = None
           Sky model image used only for QA.
        min_spat: float, default = None
           Minimum spatial pixel used for local sky subtraction fitting. Only used for title of QA plot.
        max_spat: float, defualt = None
           Maximum spatial pixel used for local sky subtraction fitting. Only used for title of QA plot.
        debug: bool, default = False
           Show QA plot to debug breakpoint placing.

    Returns:
        fullbkpt: ndarray, float
           Locations of the optimally sampled breakpoints

    """

    pix = piximg[sampmask]
    isrt = pix.argsort()
    pix = pix[isrt]
    piximg_min = pix.min()
    piximg_max = pix.max()
    bset0 = pydl.bspline(pix, nord=4, bkspace=bsp_min)
    fullbkpt_grid = bset0.breakpoints
    keep = (fullbkpt_grid >= piximg_min) & (fullbkpt_grid <= piximg_max)
    fullbkpt_grid = fullbkpt_grid[keep]
    used_grid = False
    if not bkpts_optimal:
        msgs.info('bkpts_optimal = False --> using uniform bkpt spacing spacing: bsp={:5.3f}'.format(bsp_min))
        fullbkpt = fullbkpt_grid
        used_grid = True
    else:
        piximg_temp = np.ma.array(np.copy(piximg))
        piximg_temp.mask = np.invert(sampmask)
        samplmin = np.ma.min(piximg_temp,fill_value=np.inf,axis=1)
        samplmin = samplmin[np.invert(samplmin.mask)].data
        samplmax = np.ma.max(piximg_temp,fill_value=-np.inf,axis=1)
        samplmax = samplmax[np.invert(samplmax.mask)].data
        if samplmax.size != samplmin.size:
            msgs.error('This should not happen')
        nbkpt = samplmax.size
        # Determine the sampling. dsamp represents the gap in spectral pixel (wavelength) coverage between
        # subsequent spectral direction pixels in the piximg, i.e. it is the difference between the minimum
        # value of the piximg at spectral direction pixel i+1, and the maximum value of the piximg at spectral
        # direction pixel i. A negative value dsamp < 0 implies continuous sampling with no gaps, i.e. the
        # the arc lines are sufficiently tilted that there is no sampling gap.
        dsamp_init = np.roll(samplmin, -1) - samplmax
        dsamp_init[nbkpt - 1] = dsamp_init[nbkpt - 2]
        kernel_size = int(np.fmax(np.ceil(dsamp_init.size*0.01)//2*2 + 1,15))  # This ensures kernel_size is odd
        dsamp_med = scipy.ndimage.filters.median_filter(dsamp_init, size=kernel_size, mode='reflect')
        boxcar_size = int(np.fmax(np.ceil(dsamp_med.size*0.005)//2*2 + 1,5))
        # Boxcar smooth median dsamp
        kernel = np.ones(boxcar_size)/ float(boxcar_size)
        dsamp = scipy.ndimage.convolve(dsamp_med, kernel, mode='reflect')
        # if more than samp_frac of the pixels have dsamp < bsp_min than just use a uniform breakpoint spacing
        if np.sum(dsamp <= bsp_min) > samp_frac*nbkpt:
            msgs.info('Sampling of wavelengths is nearly continuous.')
            msgs.info('Using uniform bkpt spacing: bsp={:5.3f}'.format(bsp_min))
            fullbkpt = fullbkpt_grid
            used_grid = True
        else:
            fullbkpt_orig = samplmax + dsamp/2.0
            fullbkpt_orig.sort()
            # Compute the distance between breakpoints
            dsamp_bkpt = fullbkpt_orig-np.roll(fullbkpt_orig, 1)
            dsamp_bkpt[0] = dsamp_bkpt[1]
            # Good breakpoints are those that are at least separated by our original desired bkpt spacing
            igood = dsamp_bkpt >= bsp_min
            if np.any(igood):
                fullbkpt_orig = fullbkpt_orig[igood]
            fullbkpt = fullbkpt_orig.copy()
            # Recompute the distance between breakpoints
            dsamp_bkpt = fullbkpt_orig-np.roll(fullbkpt_orig, 1)
            dsamp_bkpt[0] = dsamp_bkpt[1]
            nbkpt = fullbkpt_orig.size
            for ibkpt in range(nbkpt):
                dsamp_eff = np.fmax(dsamp_bkpt[ibkpt], bsp_min)
                # can we fit in another bkpt?
                if dsamp_bkpt[ibkpt] > 2*dsamp_eff:
                    nsmp = int(np.fmax(np.floor(dsamp_bkpt[ibkpt]/dsamp_eff),2))
                    bkpt_new = fullbkpt_orig[ibkpt - 1] + (np.arange(nsmp - 1) + 1)*dsamp_bkpt[ibkpt]/float(nsmp)
                    indx_arr = np.where(fullbkpt == fullbkpt_orig[ibkpt-1])[0]
                    if len(indx_arr) > 0:
                        indx_bkpt = indx_arr[0]
                        if indx_bkpt == 0:
                            fullbkpt = np.hstack((fullbkpt[0], bkpt_new, fullbkpt[indx_bkpt + 1:]))
                        elif indx_bkpt == (fullbkpt.size-2):
                            fullbkpt = np.hstack((fullbkpt[0:indx_bkpt], bkpt_new, fullbkpt[indx_bkpt + 1]))
                        else:
                            fullbkpt = np.hstack((fullbkpt[0:indx_bkpt], bkpt_new, fullbkpt[indx_bkpt + 1:]))

            fullbkpt.sort()
            keep = (fullbkpt >= piximg_min) & (fullbkpt <= piximg_max)
            fullbkpt = fullbkpt[keep]


    if debug:
        plt.figure(figsize=(14, 6))
        sky = skyimage[sampmask]
        sky = sky[isrt]
        # This is approximate and only for the sake of visualization:
        spat_samp_vec = np.sum(sampmask, axis=1)  # spatial sampling per spectral direction pixel
        spat_samp_med = np.median(spat_samp_vec[spat_samp_vec > 0])
        window_size = int(np.ceil(5 * spat_samp_med))
        sky_med_filt = utils.fast_running_median(sky, window_size)
        sky_bkpt_grid = np.interp(fullbkpt_grid, pix, sky_med_filt)
        sky_bkpt = np.interp(fullbkpt, pix, sky_med_filt)
        plt.clf()
        ax = plt.gca()
        ax.plot(pix, sky, color='k', marker='o', markersize=0.4, mfc='k', fillstyle='full', linestyle='None')
        # ax.plot(pix, sky_med_filt, color='cornflowerblue', label='median sky', linewidth=1.2)
        if used_grid == False:
            ax.plot(fullbkpt_grid, sky_bkpt_grid, color='lawngreen', marker='o', markersize=2.0, mfc='lawngreen',
                    fillstyle='full', linestyle='None', label='uniform bkpt grid')
            color = 'red'
            title_str = ''
        else:
            color = 'lawngreen'
            title_str = 'Used Grid: '
        ax.plot(fullbkpt, sky_bkpt, color=color, marker='o', markersize=4.0, mfc=color,
                fillstyle='full', linestyle='None', label='optimal bkpts')

        ax.set_ylim((0.99 * sky_med_filt.min(), 1.01 * sky_med_filt.max()))
        if min_spat is not None:
            plt.title(title_str + 'bkpt sampling spat pixels {:7.1f}-{:7.1f}'.format(min_spat, max_spat))
        plt.legend()
        plt.show()

    return fullbkpt


def local_skysub_extract(sciimg, sciivar, tilts, waveimg, global_sky, rn2_img, thismask, slit_left, slit_righ, sobjs,
                         spat_pix = None, bsp = 0.6, inmask = None, extract_maskwidth = 4.0, trim_edg = (3,3), std = False, prof_nsigma = None,
                         niter=4, box_rad = 7, sigrej = 3.5, bkpts_optimal=True, sn_gauss = 4.0,
                         model_full_slit=False, model_noise = True,
                         debug_bkpts = False, show_profile=False, show_resids=False):

    """Perform local sky subtraction and  extraction

     Args:

     sciimg : numpy float 2-d array (nspec, nspat)
         sky-subtracted image
     sciivar : numpy float 2-d array (nspec, nspat)
         inverse variance of sky-subtracted image
     tilts: ndarray, (nspec, nspat)
         spectral tilts
     waveimg numpy float 2-d array (nspec, nspat)
         2-d wavelength map
     global_sky : ndarray (nspec, nspat)
         Global sky model
     rn2_img:
         Image with the read noise squared per pixel
         object trace
     thismask:
     slit_left:
     slit_righ:
     sobjs:

     Optional Args:
     spat_pix: float ndarray, shape (nspec, nspat)
         Image containing the spatial location of pixels. If not input,
         it will be computed via spat_img = np.outer(np.ones(nspec), np.arange(nspat))


     bsp:
     inmask:
     extract_maskwidth: float, default = 4.0
        This parameter determines the initial size of the region in units of fwhm that will be used for local sky subtraction. This
        maskwidth is defined in the obfjind code, but is then updated here as the profile fitting improves the fwhm estimates
     trim_edg:
     std:
     prof_nsigma:
     niter:
     box_rad:
     sigrej:
     bkpts_optimal: bool, default True
         bkpts_optimal = True:
              The optimal break-point spacing will be determined directly using the optimal_bkpts function
              by measuring how well we are sampling the sky  using the piximg = (nspec-1)*yilyd. The bsp parameter
              in this case corresponds to the minimum distance between breakpoints which we allow.
         bkpts_optimal = False:
              The break-points will be chosen to have a uniform spacing in pixel units sets by the bsp parameter, i.e.
              using the bkspace functionality of the pydl bspline class:

                  bset = pydl.bspline(piximg_values, nord=4, bkspace=bsp)
                  fullbkpt = bset.breakpoints
     sn_gauss:
     model_full_slit: bool, default = False
          Set the maskwidth of the objects to be equal to the slit width/2 such that the entire slit will be modeled
          by the local skysubtraction. This mode is recommended for echelle spectra with reasonably narrow slits.
     model_noise:
     debug_bkpts:
     show_profile:
     show_resids:

     Returns:

     :func:`tuple`

    """

    if inmask is None:
        # These values are hard wired for the case where no inmask is provided
        FULLWELL = 5e5
        MINWELL = -1000.0,
        inmask = (sciivar > 0.0) & thismask & np.isfinite(sciimg) & np.isfinite(sciivar) & (sciimg < FULLWELL) & (sciimg > MINWELL)

    # Adjust maskwidths of the objects such that we will apply the local_skysub_extract to the entire slit
    if model_full_slit:
        max_slit_width = np.max(slit_righ - slit_left)
        for spec in sobjs:
            spec.maskwidth = max_slit_width/2.0

    ximg, edgmask = pixels.ximg_and_edgemask(slit_left, slit_righ, thismask, trim_edg=trim_edg)

    nspat = sciimg.shape[1]
    nspec = sciimg.shape[0]
    piximg = tilts * (nspec-1)

    # Copy the specobjs that will be the output
    nobj = len(sobjs)

    # Set up the prof_nsigma
    if (prof_nsigma is None):
        prof_nsigma1 = np.full(len(sobjs), None)
    elif len(prof_nsigma) == 1:
        prof_nsigma1 = np.full(nobj, prof_nsigma)
    elif len(prof_nsigma) == nobj:
        prof_nsigma1 = prof_nsigma
    else:
        raise ValueError('Invalid size for prof_nsigma.')

    for iobj in range(nobj):
        sobjs[iobj].prof_nsigma = prof_nsigma1[iobj]

    # Set some rejection parameters based on whether this is a standard or not. Only reject extreme outliers for standards
    # since super high S/N and low order profile models imply we will always have large outliers
    if std is True:
        chi2_sigrej = 100.0
        sigrej_ceil = 1e10
    else:
        chi2_sigrej = 6.0
        sigrej_ceil = 10.0
    # We will use this number later
    gauss_prob = 1.0 - 2.0 * ndtr(-sigrej)

    # Create the images that will be returned
    outmask = np.copy(inmask)
    modelivar = np.copy(sciivar)
    objimage = np.zeros_like(sciimg)
    skyimage = np.copy(global_sky)
    #varnoobj = np.abs(skyimage - np.sqrt(2.0) * np.sqrt(rn2_img)) + rn2_img

    # TODO Add a line of code here that updates the modelivar using the global sky if nobj = 0 and simply returns
    spec_img = np.outer(np.arange(nspec), np.ones(nspat))
    spat_img = np.outer(np.ones(nspec), np.arange(nspat))
    if spat_pix is None:
        spat_pix = spat_img

    spat_min = spat_img[thismask].min()
    spat_max = spat_img[thismask].max()
    spec_min = spec_img[thismask].min()
    spec_max = spec_img[thismask].max()

    xsize = slit_righ - slit_left
    spatial_img = thismask * ximg * (np.outer(xsize, np.ones(nspat)))

    # Loop over objects and group them
    i1 = 0
    while i1 < nobj:
        group = np.array([], dtype=np.int)
        group = np.append(group, i1)
        # The default value of maskwidth = 3.0 * FWHM = 7.05 * sigma in objfind with a log(S/N) correction for bright objects
        min_spat1 = np.maximum(sobjs[i1].trace_spat - sobjs[i1].maskwidth - 1, slit_left)
        max_spat1 = np.minimum(sobjs[i1].trace_spat + sobjs[i1].maskwidth + 1, slit_righ)
        for i2 in range(i1 + 1, nobj):
            left_edge = sobjs[i2].trace_spat - sobjs[i2].maskwidth - 1
            righ_edge = sobjs[i2].trace_spat + sobjs[i2].maskwidth + 1
            touch = (left_edge < max_spat1) & (sobjs[i2].trace_spat > slit_left) & (righ_edge > min_spat1)
            if touch.any():
                max_spat1 = np.minimum(np.maximum(righ_edge, max_spat1), slit_righ)
                min_spat1 = np.maximum(np.minimum(left_edge, min_spat1), slit_left)
                group = np.append(group, i2)
        # Keep for next iteration
        i1 = group.max() + 1
        # Some bookeeping to define the sub-image and make sure it does not land off the mask
        objwork = len(group)
        scope = np.sum(thismask, axis=0)
        iscp, = np.where(scope)
        imin = iscp.min()
        imax = iscp.max()
        min_spat = np.fmax(np.floor(min(min_spat1)), imin)
        max_spat = np.fmin(np.ceil(max(max_spat1)), imax)
        nc = int(max_spat - min_spat + 1)
        spec_vec = np.arange(nspec, dtype=np.intp)
        spat_vec = np.arange(min_spat, min_spat + nc, dtype=np.intp)
        ipix = np.ix_(spec_vec, spat_vec)
        skymask = outmask & np.invert(edgmask)
        if nc > 100:
            npoly = 3
        elif nc > 40:
            npoly = 2
        else:
            npoly = 1
        obj_profiles = np.zeros((nspec, nspat, objwork), dtype=float)
        sigrej_eff = sigrej
        for iiter in range(1, niter + 1):
            msgs.info('--------------------------REDUCING: Iteration # ' + '{:2d}'.format(iiter) + ' of ' +
                      '{:2d}'.format(niter) + '---------------------------------------------------')
            img_minsky = sciimg - skyimage
            for ii in range(objwork):
                iobj = group[ii]
                if iiter == 1:
                    # If this is the first iteration, print status message. Initiate profile fitting with a simple
                    # boxcar extraction.
                    msgs.info("----------------------------------- PROFILE FITTING --------------------------------------------------------")
                    msgs.info("Fitting profile for obj # " + "{:}".format(sobjs[iobj].objid) + " of {:}".format(nobj))
                    msgs.info("At x = {:5.2f}".format(sobjs[iobj].spat_pixpos) + " on slit # {:}".format(sobjs[iobj].slitid))
                    msgs.info("------------------------------------------------------------------------------------------------------------")
                    flux = extract.extract_boxcar(img_minsky * outmask, sobjs[iobj].trace_spat, box_rad,
                                          ycen=sobjs[iobj].trace_spec)
                    mvarimg = 1.0 / (modelivar + (modelivar == 0))
                    mvar_box = extract.extract_boxcar(mvarimg * outmask, sobjs[iobj].trace_spat, box_rad,
                                              ycen=sobjs[iobj].trace_spec)
                    pixtot = extract.extract_boxcar(0 * mvarimg + 1.0, sobjs[iobj].trace_spat, box_rad,
                                            ycen=sobjs[iobj].trace_spec)
                    mask_box = (extract.extract_boxcar(~outmask, sobjs[iobj].trace_spat, box_rad,
                                               ycen=sobjs[iobj].trace_spec) != pixtot)
                    box_denom = extract.extract_boxcar(waveimg > 0.0, sobjs[iobj].trace_spat, box_rad,
                                               ycen=sobjs[iobj].trace_spec)
                    wave = extract.extract_boxcar(waveimg, sobjs[iobj].trace_spat, box_rad, ycen=sobjs[iobj].trace_spec) / (
                                box_denom + (box_denom == 0.0))
                    fluxivar = mask_box / (mvar_box + (mvar_box == 0.0))
                else:
                    # For later iterations, profile fitting is based on an optimal extraction
                    last_profile = obj_profiles[:, :, ii]
                    trace = np.outer(sobjs[iobj].trace_spat, np.ones(nspat))
                    objmask = ((spat_img >= (trace - 2.0 * box_rad)) & (spat_img <= (trace + 2.0 * box_rad)))
                    extract.extract_optimal(sciimg, modelivar, (outmask & objmask), waveimg, skyimage, rn2_img, last_profile,
                                    box_rad, sobjs[iobj])
                    # If the extraction is bad do not update
                    if sobjs[iobj].optimal['MASK'].any():
                        flux = sobjs[iobj].optimal['COUNTS']
                        fluxivar = sobjs[iobj].optimal['COUNTS_IVAR']
                        wave = sobjs[iobj].optimal['WAVE']

                obj_string = 'obj # {:}'.format(sobjs[iobj].objid) + ' on slit # {:}'.format(sobjs[iobj].slitid) + ', iter # {:}'.format(iiter) + ':'
                if wave.any():
                    sign = sobjs[iobj].sign
                    # TODO This is "sticky" masking. Do we want it to be?
                    profile_model, trace_new, fwhmfit, med_sn2 = extract.fit_profile(
                        sign*img_minsky[ipix], (modelivar * outmask)[ipix],waveimg[ipix],spat_pix[ipix], sobjs[iobj].trace_spat,
                        wave, sign*flux, fluxivar, inmask = outmask[ipix],
                        thisfwhm=sobjs[iobj].fwhm, maskwidth=sobjs[iobj].maskwidth,
                        prof_nsigma=sobjs[iobj].prof_nsigma,sn_gauss=sn_gauss, obj_string = obj_string,
                        show_profile=show_profile)
                    #proc_list.append(show_proc)

                    # Update the object profile and the fwhm and mask parameters
                    obj_profiles[ipix[0], ipix[1], ii] = profile_model
                    sobjs[iobj].trace_spat = trace_new
                    sobjs[iobj].fwhmfit = fwhmfit
                    sobjs[iobj].fwhm = np.median(fwhmfit)
                    mask_fact = 1.0 + 0.5 * np.log10(np.fmax(np.sqrt(np.fmax(med_sn2, 0.0)), 1.0))
                    maskwidth = extract_maskwidth*np.median(fwhmfit) * mask_fact
                    if sobjs[iobj].prof_nsigma is None:
                        sobjs[iobj].maskwidth = maskwidth
                    else:
                        sobjs[iobj].maskwidth = sobjs[iobj].prof_nsigma * (sobjs[iobj].fwhm / 2.3548)

                else:
                    msgs.warn("Bad extracted wavelengths in local_skysub_extract")
                    msgs.warn("Skipping this profile fit and continuing.....")

            sky_bmodel = np.array(0.0)
            iterbsp = 0
            while (not sky_bmodel.any()) & (iterbsp <= 5):
                bsp_now = (1.2 ** iterbsp) * bsp
                ibool = (spec_img >= spec_min) & (spec_img <= spec_max) & \
                        (spat_img >= spat_min) & (spat_img <= spat_max) & \
                        (spat_img >= min_spat) & (spat_img <= max_spat) & \
                        thismask
                sampmask = (waveimg > 0.0) & ibool
                fullbkpt = optimal_bkpts(bkpts_optimal, bsp_now, piximg, sampmask, debug=(debug_bkpts & (iiter == niter)),
                                         skyimage=skyimage, min_spat=min_spat, max_spat=max_spat)
                # check to see if only a subset of the image is used.
                # if so truncate input pixels since this can result in singular matrices
                isub, = np.where(ibool.flatten())
                sortpix = (piximg.flat[isub]).argsort()
                obj_profiles_flat = obj_profiles.reshape(nspec * nspat, objwork)
                sky_bmodel, obj_bmodel, outmask_opt = skyoptimal(piximg.flat[isub], sciimg.flat[isub],
                                                                 (modelivar * skymask).flat[isub],
                                                                 obj_profiles_flat[isub, :], sortpix,
                                                                 spatial=spatial_img.flat[isub],
                                                                 fullbkpt=fullbkpt, sigrej=sigrej_eff, npoly=npoly)
                iterbsp = iterbsp + 1
                if (not sky_bmodel.any()) & (iterbsp <= 4):
                    msgs.warn('***************************************')
                    msgs.warn('WARNING: bspline sky-subtraction failed')
                    msgs.warn('Increasing bkpt spacing by 20%. Retry')
                    msgs.warn(
                        'Old bsp = {:5.2f}'.format(bsp_now) + '; New bsp = {:5.2f}'.format(1.2 ** (iterbsp) * bsp))
                    msgs.warn('***************************************')

            if sky_bmodel.any():
                skyimage.flat[isub] = sky_bmodel
                objimage.flat[isub] = obj_bmodel
                img_minsky.flat[isub] = sciimg.flat[isub] - sky_bmodel
                #var_no = np.abs(sky_bmodel - np.sqrt(2.0) * np.sqrt(rn2_img.flat[isub])) + rn2_img.flat[isub]
                igood1 = skymask.flat[isub]
                #  update the outmask for only those pixels that were fit. This prevents masking of slit edges in outmask
                outmask.flat[isub[igood1]] = outmask_opt[igood1]
                #  For weighted co-adds, the variance of the image is no longer equal to the image, and so the modelivar
                #  eqn. below is not valid. However, co-adds already have the model noise propagated correctly in sciivar,
                #  so no need to re-model the variance.
                if model_noise:
                    var = np.abs(sky_bmodel + obj_bmodel - np.sqrt(2.0) * np.sqrt(rn2_img.flat[isub])) + rn2_img.flat[isub]
                    modelivar.flat[isub] = (var > 0.0) / (var + (var == 0.0))
                    #varnoobj.flat[isub] = var_no
                # Now do some masking based on this round of model fits
                chi2 = (img_minsky.flat[isub] - obj_bmodel) ** 2 * modelivar.flat[isub]
                igood = (skymask.flat[isub]) & (chi2 <= chi2_sigrej ** 2)
                ngd = np.sum(igood)
                if ngd > 0:
                    chi2_good = chi2[igood]
                    chi2_srt = np.sort(chi2_good)
                    sigind = np.fmin(int(np.rint(gauss_prob * float(ngd))), ngd - 1)
                    chi2_sigrej = chi2_srt[sigind]
                    sigrej_eff = np.fmax(np.sqrt(chi2_sigrej), sigrej)
                    #  Maximum sigrej is sigrej_ceil (unless this is a standard)
                    sigrej_eff = np.fmin(sigrej_eff, sigrej_ceil)
                    msgs.info('Measured effective rejection from distribution of chi^2')
                    msgs.info('Instead of rejecting sigrej = {:5.2f}'.format(sigrej) +
                              ', use threshold sigrej_eff = {:5.2f}'.format(sigrej_eff))
                    # Explicitly mask > sigrej outliers using the distribution of chi2 but only in the region that was actually fit.
                    # This prevents e.g. excessive masking of slit edges
                    outmask.flat[isub[igood1]] = outmask.flat[isub[igood1]] & (chi2[igood1] < chi2_sigrej) & (
                                sciivar.flat[isub[igood1]] > 0.0)
                    nrej = outmask.flat[isub[igood1]].sum()
                    msgs.info(
                        'Iteration = {:d}'.format(iiter) + ', rejected {:d}'.format(nrej) + ' of ' + '{:d}'.format(
                            igood1.sum()) + 'fit pixels')

            else:
                msgs.warn('ERROR: Bspline sky subtraction failed after 4 iterations of bkpt spacing')
                msgs.warn('       Moving on......')
                obj_profiles = np.zeros_like(obj_profiles)
                # Just replace with the global sky
                skyimage.flat[isub] = global_sky.flat[isub]

        # Now that the iterations of profile fitting and sky subtraction are completed,
        # loop over the objwork objects in this grouping and perform the final extractions.
        for ii in range(objwork):
            iobj = group[ii]
            msgs.info('Extracting obj # {:d}'.format(iobj + 1) + ' of {:d}'.format(nobj) +
                      ' with objid = {:d}'.format(sobjs[iobj].objid) + ' on slit # {:d}'.format(sobjs[iobj].slitid) +
                      ' at x = {:5.2f}'.format(sobjs[iobj].spat_pixpos))
            this_profile = obj_profiles[:, :, ii]
            trace = np.outer(sobjs[iobj].trace_spat, np.ones(nspat))
            objmask = ((spat_img >= (trace - 2.0 * box_rad)) & (spat_img <= (trace + 2.0 * box_rad)))
            extract.extract_optimal(sciimg, modelivar * thismask, (outmask & objmask), waveimg, skyimage, rn2_img, this_profile,
                            box_rad, sobjs[iobj])
            sobjs[iobj].min_spat = min_spat
            sobjs[iobj].max_spat = max_spat


    # If requested display the model fits for this slit
    if show_resids:
        viewer, ch = ginga.show_image((sciimg - skyimage - objimage) * np.sqrt(modelivar) * thismask)
        # TODO add error checking here to see if ginga exists
        canvas = viewer.canvas(ch._chname)
        out1 = canvas.clear()
        out2 = ch.cut_levels(-5.0, 5.0)
        out3 = ch.set_color_algorithm('linear')
        # Overplot the traces
        for spec in sobjs:
            if spec.hand_extract_flag is False:
                color = 'magenta'
            else:
                color = 'orange'
            ginga.show_trace(viewer, ch, spec.trace_spat, spec.idx, color=color)

        # These are the pixels that were masked by the extraction
        spec_mask, spat_mask = np.where((outmask == False) & (inmask == True))
        nmask = len(spec_mask)
        # note: must cast numpy floats to regular python floats to pass the remote interface
        points_mask = [dict(type='point', args=(float(spat_mask[i]), float(spec_mask[i]), 2),
                            kwargs=dict(style='plus', color='red')) for i in range(nmask)]

        # These are the pixels that were originally masked
        spec_omask, spat_omask = np.where((inmask == False) & (thismask == True))
        nomask = len(spec_omask)
        # note: must cast numpy floats to regular python floats to pass the remote interface
        points_omask = [dict(type='point', args=(float(spat_omask[i]), float(spec_omask[i]), 2),
                             kwargs=dict(style='plus', color='cyan')) for i in range(nomask)]

        # Labels for the points
        text_mask = [dict(type='text', args=(nspat / 2, nspec / 2, 'masked by extraction'),
                          kwargs=dict(color='red', fontsize=20))]
        text_omask = [dict(type='text', args=(nspat / 2, nspec / 2 + 30, 'masked initially'),
                           kwargs=dict(color='cyan', fontsize=20))]

        canvas_list = points_mask + points_omask + text_mask + text_omask
        canvas.add('constructedcanvas', canvas_list)

    return (skyimage[thismask], objimage[thismask], modelivar[thismask], outmask[thismask])



def ech_local_skysub_extract(sciimg, sciivar, mask, tilts, waveimg, global_sky, rn2img, tslits_dict, sobjs, order_vec,
                             spat_pix=None, fit_fwhm=False, min_snr=2.0,
                             bsp = 0.6, extract_maskwidth = 4.0, trim_edg = (3,3), std=False, prof_nsigma=None,
                             niter=4, box_rad = 7, sigrej=3.5, bkpts_optimal=True, sn_gauss=4.0,
                             model_full_slit=False, model_noise=True, debug_bkpts=False,
                             show_profile=False, show_resids=False, show_fwhm=False):
        """
        Perform local sky subtraction, profile fitting, and optimal extraction slit by slit

        Wrapper to skysub.local_skysub_extract

        Parameters
        ----------
        sobjs: object
           Specobjs object containing Specobj objects containing information about objects found.
        waveimg: ndarray, shape (nspec, nspat)
           Wavelength map

        Optional Parameters
        -------------------


        Returns:
            global_sky: (numpy.ndarray) image of the the global sky model
        """

        bitmask = processimages.ProcessImagesBitMask()  # The bit mask interpreter

        # Allocate the images that are needed
        # Initialize to mask in case no objects were found
        slitmask = pixels.tslits2mask(tslits_dict)
        outmask = np.copy(mask)
        extractmask = (mask == 0)
        # TODO case of no objects found should be properly dealt with by local_skysub_extract
        # Initialize to zero in case no objects were found
        objmodel = np.zeros_like(sciimg)
        # Set initially to global sky in case no objects were found
        skymodel  = np.copy(global_sky)
        # Set initially to sciivar in case no obects were found.
        ivarmodel = np.copy(sciivar)
        sobjs = sobjs.copy()

        norders = tslits_dict['nslits']
        slit_vec = np.arange(norders)

        if (np.sum(sobjs.sign > 0) % norders) == 0:
            nobjs = int((np.sum(sobjs.sign > 0)/norders))
        else:
            msgs.error('Number of specobjs in sobjs is not an integer multiple of the number or ordres!')

        order_snr = np.zeros((norders, nobjs))
        uni_objid = np.unique(sobjs[sobjs.sign > 0].ech_objid)
        for iord in range(norders):
            for iobj in range(nobjs):
                ind = (sobjs.ech_orderindx == iord) & (sobjs.ech_objid == uni_objid[iobj])
                order_snr[iord,iobj] = sobjs[ind].ech_snr

        # Compute the average SNR and find the brightest object
        snr_bar = np.mean(order_snr,axis=0)
        srt_obj = snr_bar.argsort()[::-1]
        ibright = srt_obj[0] # index of the brightest object
        # Now extract the orders in descending order of S/N for the brightest object
        srt_order_snr = order_snr[:,ibright].argsort()[::-1]
        fwhm_here = np.zeros(norders)
        fwhm_was_fit = np.zeros(norders,dtype=bool)
        # Print out a status message
        str_out = ''
        for iord in srt_order_snr:
            str_out += '{:<8d}{:<8d}{:>10.2f}'.format(slit_vec[iord], order_vec[iord], order_snr[iord,ibright]) + msgs.newline()
        dash = '-'*27
        dash_big = '-'*40
        msgs.info(msgs.newline() + 'Reducing orders in order of S/N of brightest object:' + msgs.newline() + dash +
                  msgs.newline() + '{:<8s}{:<8s}{:>10s}'.format('slit','order','S/N') + msgs.newline() + dash +
                  msgs.newline() + str_out)
        # Loop over orders in order of S/N ratio (from highest to lowest) for the brightest object
        for iord in srt_order_snr:
            order = order_vec[iord]
            msgs.info("Local sky subtraction and extraction for slit/order: {:d}/{:d}".format(iord,order))
            other_orders = (fwhm_here > 0) & np.invert(fwhm_was_fit)
            other_fit    = (fwhm_here > 0) & fwhm_was_fit
            # Loop over objects in order of S/N ratio (from highest to lowest)
            for iobj in srt_obj:
                if (order_snr[iord, iobj] <= min_snr) & (np.sum(other_orders) >= 3):
                    if iobj == ibright:
                        # If this is the brightest object then we extrapolate the FWHM from a fit
                        #fwhm_coeffs = np.polyfit(order_vec[other_orders], fwhm_here[other_orders], 1)
                        #fwhm_fit_eval = np.poly1d(fwhm_coeffs)
                        #fwhm_fit = fwhm_fit_eval(order_vec[iord])
                        fwhm_was_fit[iord] = True
                        # Either perform a linear fit to the FWHM or simply take the median
                        if fit_fwhm:
                            minx = 0.0
                            maxx = fwhm_here[other_orders].max()
                            # ToDO robust_poly_fit needs to return minv and maxv as outputs for the fits to be usable downstream
                            fit_mask, fwhm_coeffs = utils.robust_polyfit_djs(order_vec[other_orders], fwhm_here[other_orders],1,
                                                                            function='polynomial',maxiter=25,lower=2.0, upper=2.0,
                                                                            maxrej=1,sticky=False, minx=minx, maxx=maxx)
                            fwhm_this_ord = utils.func_val(fwhm_coeffs, order_vec[iord], 'polynomial', minx=minx, maxx=maxx)
                            fwhm_all = utils.func_val(fwhm_coeffs, order_vec, 'polynomial', minx=minx, maxx=maxx)
                            fwhm_str = 'linear fit'
                        else:
                            fit_mask = np.ones_like(order_vec[other_orders],dtype=bool)
                            fwhm_this_ord = np.median(fwhm_here[other_orders])
                            fwhm_all = np.full(norders,fwhm_this_ord)
                            fwhm_str = 'median '
                        indx = (sobjs.ech_objid == uni_objid[iobj]) & (sobjs.ech_orderindx == iord)
                        for spec in sobjs[indx]:
                            spec.fwhm = fwhm_this_ord

                        str_out = ''
                        for slit_now, order_now, snr_now, fwhm_now in zip(slit_vec[other_orders], order_vec[other_orders],order_snr[other_orders,ibright], fwhm_here[other_orders]):
                            str_out += '{:<8d}{:<8d}{:>10.2f}{:>10.2f}'.format(slit_now, order_now, snr_now, fwhm_now) + msgs.newline()
                        msgs.info(msgs.newline() + 'Using' +  fwhm_str + ' for FWHM of object={:d}'.format(uni_objid[iobj]) +
                                  ' on slit/order: {:d}/{:d}'.format(iord,order) + msgs.newline() + dash_big +
                                  msgs.newline() + '{:<8s}{:<8s}{:>10s}{:>10s}'.format('slit', 'order','SNR','FWHM') +
                                  msgs.newline() + dash_big +
                                  msgs.newline() + str_out[:-8] +
                                  fwhm_str.upper() +  ':{:<8d}{:<8d}{:>10.2f}{:>10.2f}'.format(iord, order, order_snr[iord,ibright], fwhm_this_ord) +
                                  msgs.newline() + dash_big)
                        if show_fwhm:
                            plt.plot(order_vec[other_orders][fit_mask], fwhm_here[other_orders][fit_mask], marker='o', linestyle=' ',
                            color='k', mfc='k', markersize=4.0, label='orders informing fit')
                            if np.any(np.invert(fit_mask)):
                                plt.plot(order_vec[other_orders][np.invert(fit_mask)],
                                         fwhm_here[other_orders][np.invert(fit_mask)], marker='o', linestyle=' ',
                                         color='magenta', mfc='magenta', markersize=4.0, label='orders rejected by fit')
                            if np.any(other_fit):
                                plt.plot(order_vec[other_fit], fwhm_here[other_fit], marker='o', linestyle=' ',
                                color='lawngreen', mfc='lawngreen',markersize=4.0, label='fits to other low SNR orders')
                            plt.plot([order_vec[iord]], [fwhm_this_ord], marker='o', linestyle=' ',color='red', mfc='red', markersize=6.0,label='this order')
                            plt.plot(order_vec, fwhm_all, color='cornflowerblue', zorder=10, linewidth=2.0, label=fwhm_str)
                            plt.legend()
                            plt.show()
                    else:
                        # If this is not the brightest object then assign it the FWHM of the brightest object
                        indx     = np.where((sobjs.ech_objid == uni_objid[iobj]) & (sobjs.ech_orderindx == iord))[0][0]
                        indx_bri = np.where((sobjs.ech_objid == uni_objid[ibright]) & (sobjs.ech_orderindx == iord))[0][0]
                        spec = sobjs[indx]
                        spec.fwhm = sobjs[indx_bri].fwhm

            thisobj = (sobjs.ech_orderindx == iord) # indices of objects for this slit
            thismask = (slitmask == iord) # pixels for this slit
            # True  = Good, False = Bad for inmask
            inmask = (mask == 0) & thismask
            # Local sky subtraction and extraction
            skymodel[thismask], objmodel[thismask], ivarmodel[thismask], extractmask[thismask] = local_skysub_extract(
                sciimg, sciivar, tilts, waveimg, global_sky,rn2img, thismask,
                tslits_dict['slit_left'][:,iord],tslits_dict['slit_righ'][:, iord], sobjs[thisobj], spat_pix=spat_pix,
                inmask=inmask,std = std, bsp=bsp, extract_maskwidth=extract_maskwidth, trim_edg=trim_edg,
                prof_nsigma=prof_nsigma, niter=niter, box_rad=box_rad, sigrej=sigrej, bkpts_optimal=bkpts_optimal,
                sn_gauss=sn_gauss, model_full_slit=model_full_slit, model_noise=model_noise, debug_bkpts=debug_bkpts,
                show_resids=show_resids, show_profile=show_profile)
            # update the FWHM fitting vector for the brighest object
            indx = (sobjs.ech_objid == uni_objid[ibright]) & (sobjs.ech_orderindx == iord)
            fwhm_here[iord] = np.median(sobjs[indx].fwhmfit)
            # Did the FWHM get updated by the profile fitting routine in local_skysub_extract? If so, include this value
            # for future fits
            if np.abs(fwhm_here[iord] - sobjs[indx].fwhm) >= 0.01:
                fwhm_was_fit[iord] = False

        # Set the bit for pixels which were masked by the extraction.
        # For extractmask, True = Good, False = Bad
        iextract = (mask == 0) & (extractmask == False)
        # Undefined inverse variances
        outmask[iextract] = bitmask.turn_on(outmask[iextract], 'EXTRACT')

        # Return
        return skymodel, objmodel, ivarmodel, outmask, sobjs


