import numpy as np
import astropy.stats
import scipy.interpolate
import scipy.signal

from astropy import units as u
from astropy.io import fits
from astropy.table import Table
from linetools.spectra import xspectrum1d
from matplotlib import pyplot as plt


def new_wave_grid(waves, method='None'):
	""" Create a new wavelength grid for the
	spectra to be rebinned and coadded on

	Parameters
	----------
	waves : ndarray
		Set of N original wavelength arrays
	method : str, optional
		Desired method for creating new wavelength grid.
		Defaults to using the first wavelength array
		as the master wavelength grid, with options
		of a constant velocity or constant pixel grid

	Returns
	-------
	wave_grid : array
		New wavelength grid
	"""
    # Eventually add/change this to also take in slf, which has
    # slf._argflag['reduce']['pixelsize'] = 2.5?? This won't work
    # if running coadding outside of PYPIT, which we'd like as an
    # option!

    if method == 'velocity': # Constant km/s
        # Loop over spectra and save wavelength arrays to find min, max of
        # wavelength grid
        wave_grid_min = np.min(waves)
        wave_grid_max = np.max(waves)

        wave_grid = [wave_grid_min]
        count = 0

        while max(wave_grid) <= wave_grid_max:
            # How do we determine a reasonable constant velocity? (the 100. here is arbitrary)
            step = wave_grid[count] * (100. / 299792.458)
            wave_grid.append(wave_grid[count] + step)
            count += 1

        wave_grid = np.asarray(wave_grid)

    elif method == 'pixel': # Constant Angstrom
        wave_grid_min = np.min(np.array(waves))
        wave_grid_max = np.max(np.array(waves))

        pix_size = 2.5 #slf._argflag['reduce']['pixelsize']
        constant_A = pix_size*1.02 # 1.02 here is the A/pix for this instrument; stored in slf. somewhere?
        wave_grid = np.arange(wave_grid_min, wave_grid_max + constant_A, constant_A)

    else:
        wave_grid = waves[0]
    # Concatenate of any wavelengths in other indices that may extend beyond that of wavelengths[0]?

    return wave_grid

def gauss1(x, parameters):
    sz = x.shape[0]
    
    if sz+1 == 5:
        smax = float(26)
    else:
        smax = 13.
    
    if len(parameters) >= 3:
        norm = parameters[2]
    else:
        norm = 1.
        
    u = ( (x - parameters[0]) / max(np.abs(parameters[1]), 1e-20) )**2.
    
    x_mask = np.where(u < smax**2)[0]
    norm = norm / (np.sqrt(2. * np.pi)*parameters[1])
                   
    return norm * x_mask * np.exp(-0.5 * u * x_mask)

def sn_weight(new_wave, fluxes, variances):
    """ Calculate the S/N of each input spectrum and
	create an array of weights by which to weight the
	spectra by in coadding

	Parameters
	----------
	new_wave : array
		New wavelength grid
	fluxes : ndarray
		Flux arrays of the input spectra
	variances : ndarray
		Variances of the input spectra

	Returns
	-------
	sn2 : array
		Mean S/N^2 value for each input spectra
	weights : ndarray
		Weights to be applied to the spectra
	"""

    sn2_val = (fluxes**2.) * (1./variances)
    sn2_sigclip = astropy.stats.sigma_clip(sn2_val, sigma=3, iters=1)
    sn2 = np.mean(sn2_sigclip, axis=1) #S/N^2 value for each spectrum

    mean_sn = np.sqrt(np.sum(sn2)/sn2.shape[0]) #Mean S/N value for all spectra

    if mean_sn <= 4.0:
        print "Using constant weights for coadding, mean S/N =", float("{0:.3f}".format(mean_sn))
        weights = np.outer(np.asarray(sn2), np.ones(fluxes.shape[1]))

    else:
        print "Using wavelength dependent weights for coadding"
        sn2_med1 = np.ones((fluxes.shape[0], fluxes.shape[1]))
        weights = np.ones((fluxes.shape[0], fluxes.shape[1]))

        bkspace = (10000.0/3.0e5) / (np.log(10.0))
        med_width = new_wave.shape[0] / ((np.max(new_wave) - np.min(new_wave)) / bkspace)
        sig_res = max(med_width, 3)
        nhalf = long(sig_res) * 4L
        xkern = np.arange(0, 2*nhalf+2, dtype='float64')-nhalf

        for spec in range(fluxes.shape[0]):
            sn2_med1[spec] = scipy.signal.medfilt(sn2_val[spec], kernel_size = 3)
        
        yvals = gauss1(xkern, [0.0, sig_res, 1, 0])

        for spec in range(fluxes.shape[0]):
            weights[spec] = scipy.ndimage.filters.convolve(sn2_med1[spec], yvals)
        
    return sn2, weights


def grow_mask(initial_mask, n_grow):
    """ Grows sigma-clipped mask by n_grow pixels
	on each side

	Parameters
	----------
	initial_mask : mask
		Initial mask for the flux + variance arrays
	n_grow : int
		Number of pixels to grow the initial mask by
		on each side. Defaults to 1 pixel

	Returns
	-------
	grow_mask : mask
		Final mask for the flux + variance arrays
	"""
    
    bad_pix_spec = np.where(initial_mask == True)[0]
    bad_pix_loc = np.where(initial_mask == True)[1]
    
    grow_mask = np.ma.copy(initial_mask)
    
    if len(bad_pix_spec) > 0:
        
        for i in range(0, len(bad_pix_spec)):
            
            if initial_mask[bad_pix_spec[i]][bad_pix_loc[i]]:
                if bad_pix_loc[i] == 0:
                    grow_mask[bad_pix_spec[i]][bad_pix_loc[i]+n_grow] = True
                    
                elif bad_pix_loc[i] == initial_mask.shape[1]-1:
                    grow_mask[bad_pix_spec[i]][bad_pix_loc[i]-n_grow] = True
                    
                else:
                    grow_mask[bad_pix_spec[i]][bad_pix_loc[i]-n_grow] = True
                    grow_mask[bad_pix_spec[i]][bad_pix_loc[i]+n_grow] = True
                
    return grow_mask

def sigma_clip(fluxes, variances, sn2, n_grow_mask=1):
    """ Sigma-clips the flux arrays.

	Parameters
	----------
	initial_mask : mask
		Initial mask for the flux + variance arrays
	n_grow_mask : int
		Number of pixels to grow the initial mask by
		on each side. Defaults to 1 pixel

	Returns
	-------
	grow_mask : mask
		Final mask for the flux + variance arrays
	"""
    from functools import reduce

    first_mask = np.ma.getmaskarray(fluxes)
    highest_sn_idx = np.argmax(sn2)

    base_sharp_chi = (fluxes - fluxes[highest_sn_idx]) / (np.sqrt(variances + variances[highest_sn_idx]))
    
    bad_pix = []
    
    for row in range(0, base_sharp_chi.shape[0]):
        bad_pix.append(np.where(np.abs(base_sharp_chi[row]) > 3*np.std(base_sharp_chi, axis=1)[row])[0])

    all_bad_pix = reduce(np.union1d, (np.asarray(bad_pix)))

    for idx in range(len(all_bad_pix)):
        spec_to_mask = np.argmax(np.abs(fluxes[:, all_bad_pix[idx]]))
        print "Masking pixel", all_bad_pix[idx], "in exposure", spec_to_mask+1
        first_mask[spec_to_mask][all_bad_pix[idx]] = True

    final_mask = grow_mask(first_mask, n_grow=n_grow_mask)
    
    return final_mask

def one_d_coadd(wavelengths, fluxes, variances, sig_clip=False, wave_grid_method=None):
    """ Performs a coadding of the spectra in 1D.

	Parameters
	----------
	wavelengths : nd masked array
		Wavelength arrays of the input spectra
	fluxes : nd masked array
		Flux arrays of the input spectra
	variances : nd masked array
		Variances of the input spectra
	sig_clip : optional
		Perform sigma-clipping of arrays. Defaults to
		no sigma-clipping

	Returns
	-------
	fluxes : nd masked array
		Original flux arrays of the input spectra
	variances : nd masked array
		Original variances of the input spectra
	new_wave : array
		New wavelength grid
	new_flux : array
		Coadded flux array
	new_var : array
		Variance of coadded spectrum
	"""
    new_wave = new_wave_grid(wavelengths, method=wave_grid_method)
    
    sn2, weights = sn_weight(new_wave, fluxes, variances)
    
    inv_variances = 1./variances
    
    for spec in range(fluxes.shape[0]):
        obj = xspectrum1d.XSpectrum1D.from_tuple((np.ma.getdata(wavelengths[spec]), np.ma.getdata(fluxes[spec]), np.sqrt(np.ma.getdata(variances[spec]))))
        obj = obj.rebin(new_wave*u.AA, do_sig=True)
        fluxes[spec] = np.ma.array(obj.flux)
        variances[spec] = np.ma.array(obj.sig)**2.

    if sig_clip:
        final_mask = sigma_clip(fluxes, variances, sn2)

        weights = np.ma.array(weights, mask=final_mask)
        fluxes = np.ma.array(fluxes, mask=final_mask)
        variances = np.ma.array(variances, mask=final_mask)
    
    else:
        final_mask = np.ma.getmaskarray(fluxes)
        weights = np.ma.array(weights, mask=final_mask)
        
    sum_weights = np.ma.sum(weights, axis=0)

    new_flux = np.ma.sum(weights*fluxes, axis=0) / (sum_weights + (sum_weights == 0.0).astype(int))
    var = (inv_variances != 0.0).astype(float) / (inv_variances + (inv_variances == 0.0).astype(float))
    new_var = np.ma.sum((weights**2.)*var, axis=0) / ((sum_weights + (sum_weights == 0.0).astype(int))**2.)        

    return fluxes, variances, new_wave, new_flux, new_var

def qa_plots(wavelengths, fluxes, variances, new_wave, new_flux, new_var):
    from matplotlib.backends.backend_pdf import PdfPages
    
    qa_plots = PdfPages(target + instru + '.pdf')
    
    plt.figure()

    dev_sig = (np.ma.getdata(fluxes) - new_flux) / (np.sqrt(np.ma.getdata(variances) + new_var))
    dev_sig_clip = astropy.stats.sigma_clip(dev_sig, sigma=4, iters=2)
    std_dev_devsig = np.std(dev_sig_clip)
    flat_dev_sig = dev_sig_clip.flatten()

    xmin = -10
    xmax = 10
    n_bins = 100

    hist, edges = np.histogram(flat_dev_sig, range=(xmin, xmax), bins=n_bins)
    area = len(flat_dev_sig)*((xmax-xmin)/float(n_bins))
    xppf = np.linspace(scipy.stats.norm.ppf(0.0001), scipy.stats.norm.ppf(0.9999), 100)
    plt.plot(xppf, area*scipy.stats.norm.pdf(xppf), color='black', linewidth=2.0)
    plt.gca().bar(edges[:-1], hist, width=((xmax-xmin)/float(n_bins)), alpha=0.5)
    plt.title(std_dev_devsig)
    plt.savefig(qa_plots, format='pdf')


    plt.figure()
    plt.subplots(figsize=(15,8))

    for spec in range(len(wavelengths)):
        if spec == 0:
            line_color = 'blue'
        elif spec == 1:
            line_color = 'green'
        elif spec == 2:
            line_color = 'red'

        plt.plot(wavelengths[spec], fluxes[spec], color=line_color, alpha=0.5, label='individual exposure')

    plt.plot(new_wave, new_flux, color='black', label='coadded spectrum')
    plt.legend()
    plt.title('Coadded + Original Spectra')
    plt.savefig(qa_plots, format='pdf')
    

    qa_plots.close()
    
    return

def save_coadd(new_wave, new_flux, new_var, outfil):
    """ Saves the coadded spectrum as a .fits file

	Parameters
	----------
	new_wave : array
		New wavelength grid
	new_flux : array
		Coadded flux array
	new_var : array
		Variance of coadded spectrum
	outfil : str, optional
		Name of the coadded spectrum
	"""
    prihdr = fits.Header()
    prihdu = fits.PrimaryHDU(header=prihdr)
    
    col1 = fits.Column(array=new_wave, name='box_wave', format='f8')
    col2 = fits.Column(array=new_flux, name='box_flam_coadd', format='f8')
    col3 = fits.Column(array=new_var, name='box_flam_coadd_var', format='f8')
    cols = fits.ColDefs([col1, col2, col3])
    tbhdu = fits.BinTableHDU.from_columns(cols)

    thdulist = fits.HDUList([prihdu, tbhdu])
    thdulist.writeto(outfil + '.fits', clobber=True)
    
    return

def coadd_spectra(files, extensions=None, wave_grid_method=None, sig_clip=False, outfil='coadded_spectrum.fits'):
    wavelengths = []
    fluxes = []
    variances = []
    traces = []

    if extensions is None:
        extensions = np.ones(len(files), dtype='int8')
    else:
        extensions = np.array(extensions)

    for exposure in range(len(files)):
        spectrum = fits.open(files[exposure])

        #traces.append(spectrum[0].header['EXTNAME'])[1:4]

		wavelengths.append(spectrum[extensions[exposure]].data['box_wave'])
        fluxes.append(spectrum[extensions[exposure]].data['box_flam'])
		variances.append(spectrum[extensions[exposure]].data['box_flam_var'])

	wavelengths = np.ma.vstack([wavelengths])
	fluxes = np.ma.vstack([fluxes])
	variances = np.ma.vstack([variances])

	# Add check on trace location here (to make sure the trace location of objects is similar, and thus likely the same object)

    masked_fluxes, masked_vars, new_wave, new_flux, new_var = one_d_coadd(wavelengths, fluxes, variances, sig_clip=sig_clip, wave_grid=wave_grid_method)

	dev_sig = (np.ma.getdata(masked_fluxes) - new_flux) / (np.sqrt(np.ma.getdata(masked_vars) + new_var))
	std_dev = np.std(astropy.stats.sigma_clip(dev_sig, sigma=4, iters=2))
	var_corr = std_dev
	iters = 0

	while np.absolute(std_dev - 1.) >= 0.1 and iters < 4:
        print "Variance correction:", "{0:.3f}".format(var_corr), "\n"
        print "Iterating on coadding..."
        masked_fluxes, masked_vars, new_wave, new_flux, new_var = one_d_coadd(wavelengths, masked_fluxes, var_corr*masked_vars, wave_grid=wave_grid_method)
        dev_sig = (np.ma.getdata(masked_fluxes) - new_flux) / (np.sqrt(np.ma.getdata(masked_vars) + new_var))
        std_dev = np.std(astropy.stats.sigma_clip(dev_sig, sigma=4, iters=2))
        var_corr = var_corr * np.std(astropy.stats.sigma_clip(dev_sig, sigma=5, iters=2))

        print "New standard deviation:", "{0:.3f}".format(std_dev)

        # Incorporate saving of each dev/sig panel onto one page? Currently only saves last fit

	    qa_plots(wavelengths, masked_fluxes, masked_vars, new_wave, new_flux, new_var)
        iters = iters + 1

    if iters == 0:
        print "No iterations on coadding done"
        qa_plots(wavelengths, masked_fluxes, masked_vars, new_wave, new_flux, new_var)
	    
    elif iters > 0:
        print "Final correction to initial variances:", "{0:.3f}".format(var_corr), "\n"

    save_coadd(new_wave, new_flux, new_var, outfil)

    return