# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals
import numpy as np
from . import im_core


def overlay_alpha_layers(layers, keepalpha=True, dtype=np.float32):
    """
    Stacks a sequences of layers on top of one another. The first item is the
    topmost layer and the last item is the bottommost layer.

    Args:
        layers (Sequence[ndarray]): stack of images
        keepalpha (bool): if False, the alpha channel is removed after blending
        dtype (np.dtype): format for blending computation (defaults to float32)

    References:
        http://stackoverflow.com/questions/25182421/overlay-numpy-alpha
        https://en.wikipedia.org/wiki/Alpha_compositing#Alpha_blending

    Example:
        >>> import kwimage
        >>> keys = ['astro', 'carl', 'stars']
        >>> layers = [kwimage.grab_test_image(k, dsize=(100, 100)) for k in keys]
        >>> layers = [kwimage.ensure_alpha_channel(g, alpha=.5) for g in layers]
        >>> stacked = overlay_alpha_layers(layers)
        >>> # xdoctest: +REQUIRES(--show)
        >>> import kwplot
        >>> kwplot.autompl()
        >>> kwplot.imshow(stacked)
        >>> kwplot.show_if_requested()
    """
    layer_iter = iter(layers)
    raster = next(layer_iter)
    for img2 in layer_iter:
        raster = overlay_alpha_images(raster, img2, keepalpha=True,
                                      dtype=dtype)
    if not keepalpha:
        raster = raster[..., 0:3]
    return raster


def overlay_alpha_images(img1, img2, keepalpha=True, dtype=np.float32,
                         impl='inplace'):
    """
    Places img1 on top of img2 respecting alpha channels.
    Works like the Photoshop layers with opacity.

    Args:
        img1 (ndarray): top image to overlay over img2
        img2 (ndarray): base image to superimpose on
        keepalpha (bool): if False, the alpha channel is removed after blending
        dtype (np.dtype): format for blending computation (defaults to float32)

    TODO:
        - [ ] Make fast C++ version of this function

    References:
        http://stackoverflow.com/questions/25182421/overlay-numpy-alpha
        https://en.wikipedia.org/wiki/Alpha_compositing#Alpha_blending

    Example:
        >>> import kwimage
        >>> img1 = kwimage.grab_test_image('astro', dsize=(100, 100))
        >>> img2 = kwimage.grab_test_image('carl', dsize=(100, 100))
        >>> img1 = kwimage.ensure_alpha_channel(img1, alpha=.5)
        >>> img3 = overlay_alpha_images(img1, img2)
        >>> # xdoctest: +REQUIRES(--show)
        >>> import kwplot
        >>> kwplot.autompl()
        >>> kwplot.imshow(img3)
        >>> kwplot.show_if_requested()
    """
    rgb1, alpha1 = _prep_rgb_alpha(img1, dtype=dtype)
    rgb2, alpha2 = _prep_rgb_alpha(img2, dtype=dtype)

    # Perform the core alpha blending algorithm
    if impl == 'simple':
        rgb3, alpha3 = _alpha_blend_simple(rgb1, alpha1, rgb2, alpha2)
    elif impl == 'inplace':
        rgb3, alpha3 = _alpha_blend_inplace(rgb1, alpha1, rgb2, alpha2)
    elif impl == 'numexpr1':
        rgb3, alpha3 = _alpha_blend_numexpr1(rgb1, alpha1, rgb2, alpha2)
    elif impl == 'numexpr2':
        rgb3, alpha3 = _alpha_blend_numexpr2(rgb1, alpha1, rgb2, alpha2)
    else:
        raise ValueError('unknown impl={}'.format(impl))

    if keepalpha:
        img3 = np.dstack([rgb3, alpha3[..., None]])
        # Note: if we want to output a 255 img we could do something like this
        # out = np.zeros_like(img1)
        # out[..., :3] = rgb3
        # out[..., 3] = alpha3
    else:
        img3 = rgb3
    return img3


def _prep_rgb_alpha(img, dtype=np.float32):
    img = im_core.ensure_float01(img, dtype=dtype, copy=False)
    img = im_core.atleast_3channels(img, copy=False)
    c = im_core.num_channels(img)

    if c == 4:
        # rgb = np.ascontiguousarray(img[..., 0:3])
        # alpha = np.ascontiguousarray(img[..., 3])
        rgb = img[..., 0:3]
        alpha = img[..., 3]
    else:
        rgb = img
        alpha = np.ones_like(img[..., 0])
    return rgb, alpha


def _alpha_blend_simple(rgb1, alpha1, rgb2, alpha2):
    """
    Core alpha blending algorithm

    SeeAlso:
        _alpha_blend_inplace - alternative implementation
    """
    c_alpha1 = (1.0 - alpha1)
    alpha3 = alpha1 + alpha2 * c_alpha1

    numer1 = (rgb1 * alpha1[..., None])
    numer2 = (rgb2 * (alpha2 * c_alpha1)[..., None])
    with np.errstate(invalid='ignore'):
        rgb3 = (numer1 + numer2) / alpha3[..., None]
    rgb3[alpha3 == 0] = 0
    return rgb3, alpha3


def _alpha_blend_numexpr1(rgb1, alpha1, rgb2, alpha2):
    """ Alternative. Not well optimized """
    import numexpr
    alpha1_ = alpha1[..., None]  # NOQA
    alpha2_ = alpha2[..., None]  # NOQA
    alpha3 = numexpr.evaluate('alpha1 + alpha2 * (1.0 - alpha1)')
    alpha3_ = alpha3[..., None]  # NOQA
    rgb3 = numexpr.evaluate('((rgb1 * alpha1_) + (rgb2 * alpha2_ * (1.0 - alpha1_))) / alpha3_')
    rgb3[alpha3 == 0] = 0


def _alpha_blend_numexpr2(rgb1, alpha1, rgb2, alpha2):
    """ Alternative. Not well optimized """
    import numexpr
    c_alpha1 = numexpr.evaluate('1.0 - alpha1')
    alpha3 = numexpr.evaluate('alpha1 + alpha2 * c_alpha1')

    c_alpha1_ = c_alpha1[..., None]  # NOQA
    alpha1_ = alpha1[..., None]  # NOQA
    alpha2_ = alpha2[..., None]  # NOQA
    alpha3_ = alpha3[..., None]  # NOQA

    numer1 = numexpr.evaluate('rgb1 * alpha1_')  # NOQA
    numer2 = numexpr.evaluate('rgb2 * (alpha2_ * c_alpha1_)')  # NOQA
    with np.errstate(invalid='ignore'):
        rgb3 = numexpr.evaluate('(numer1 + numer2) / alpha3_')
    rgb3[alpha3 == 0] = 0
    return rgb3, alpha3


def _alpha_blend_inplace(rgb1, alpha1, rgb2, alpha2):
    """
    Uglier but faster(? maybe not) version of the core alpha blending algorithm
    using preallocation and in-place computation where possible.

    SeeAlso:
        _alpha_blend_simple - alternative implementation

    Example:
        >>> rng = np.random.RandomState(0)
        >>> rgb1, rgb2 = rng.rand(10, 10, 3), rng.rand(10, 10, 3)
        >>> alpha1, alpha2 = rng.rand(10, 10), rng.rand(10, 10)
        >>> f1, f2 = _alpha_blend_inplace(rgb1, alpha1, rgb2, alpha2)
        >>> s1, s2 = _alpha_blend_simple(rgb1, alpha1, rgb2, alpha2)
        >>> assert np.all(f1 == s1) and np.all(f2 == s2)
        >>> alpha1, alpha2 = np.zeros((10, 10)), np.zeros((10, 10))
        >>> f1, f2 = _alpha_blend_inplace(rgb1, alpha1, rgb2, alpha2)
        >>> s1, s2 = _alpha_blend_simple(rgb1, alpha1, rgb2, alpha2)
        >>> assert np.all(f1 == s1) and np.all(f2 == s2)
    """
    rgb3 = np.empty_like(rgb1)
    temp_rgb = np.empty_like(rgb1)
    alpha3 = np.empty_like(alpha1)
    temp_alpha = np.empty_like(alpha1)

    # hold (1 - alpha1)
    np.subtract(1, alpha1, out=temp_alpha)

    # alpha3
    np.copyto(dst=alpha3, src=temp_alpha)
    np.multiply(alpha2, alpha3, out=alpha3)
    np.add(alpha1, alpha3, out=alpha3)

    # numer1
    np.multiply(rgb1, alpha1[..., None], out=rgb3)

    # numer2
    np.multiply(alpha2, temp_alpha, out=temp_alpha)
    np.multiply(rgb2, temp_alpha[..., None], out=temp_rgb)

    # (numer1 + numer2)
    np.add(rgb3, temp_rgb, out=rgb3)

    # removing errstate is actually a significant speedup
    with np.errstate(invalid='ignore'):
        np.divide(rgb3, alpha3[..., None], out=rgb3)
    if not np.all(alpha3):
        rgb3[alpha3 == 0] = 0
    return rgb3, alpha3


def ensure_alpha_channel(img, alpha=1.0):
    """
    Returns the input image with 4 channels.
    """
    img = im_core.ensure_float01(img, copy=False)
    c = im_core.num_channels(img)
    if c == 4:
        return img
    else:
        if isinstance(alpha, np.ndarray):
            alpha_channel = alpha
        else:
            alpha_channel = np.full(img.shape[0:2], fill_value=alpha, dtype=img.dtype)
        if c == 3:
            return np.dstack([img, alpha_channel])
        elif c == 1:
            return np.dstack([img, img, img, alpha_channel])
        else:
            raise ValueError('unknown dim')
