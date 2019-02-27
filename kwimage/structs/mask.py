"""
Data structure for Binary Masks

Structure for efficient encoding of per-annotation segmentation masks
Based on efficient cython/C code in the cocoapi [1].

THIS IS CURRENTLY A WORK IN PROGRESS.

References:
    ..[1] https://github.com/nightrome/cocostuffapi/blob/master/PythonAPI/pycocotools/_mask.pyx
    ..[2] https://github.com/nightrome/cocostuffapi/blob/master/common/maskApi.c
    ..[3] https://github.com/nightrome/cocostuffapi/blob/master/common/maskApi.h
    ..[4] https://github.com/nightrome/cocostuffapi/blob/master/PythonAPI/pycocotools/mask.py

Goals:
    The goal of this file is to create a datastructure that lets the developer
    seemlessly convert between:
        (1) raw binary uint8 masks
        (2) memory-efficient comprsssed run-length-encodings of binary
        segmentation masks.
        (3) convex polygons
        (4) convex hull polygons
        (5) bounding box

    It is not there yet, and the API is subject to change in order to better
    accomplish these goals.
"""
import cv2
import copy
import numpy as np
import ubelt as ub
import itertools as it
from kwimage.structs._mask_backend import cython_mask

__all__ = ['Mask']


class MaskFormat:
    """
    Defines valid formats and their aliases.

    Attrs:
        aliases (Mapping[str, str]):
            maps format aliases to their cannonical name.
    """
    cannonical = []

    def _register(k, cannonical=cannonical):
        cannonical.append(k)
        return k

    BYTES_RLE = _register('bytes_rle')  # cython compressed RLE
    ARRAY_RLE = _register('array_rle')  # numpy uncompreesed RLE
    C_MASK    = _register('c_mask')     # row-major raw binary mask
    F_MASK    = _register('f_mask')     # column-major raw binary mask

    aliases = {
    }
    for key in cannonical:
        aliases[key] = key


class _MaskConversionMixin(object):
    """
    Mixin class registering conversion functions

    For conversion speeds look into:
        ~/code/kwimage/dev/bench_rle.py
    """
    convert_funcs = {}

    def _register_convertor(key, convert_funcs=convert_funcs):
        def _reg(func):
            convert_funcs[key] = func
            return func
        return _reg

    def toformat(self, format, copy=False):
        """
        Changes the internal representation using one of the registered
        convertor functions.

        Args:
            format (str):
                the string code for the format you want to transform into.

        Example:
            >>> from kwimage.structs.mask import MaskFormat  # NOQA
            >>> mask = Mask.random(shape=(8, 8), rng=0)
            >>> # Test that we can convert to and from all formats
            >>> for format1 in MaskFormat.cannonical:
            ...     mask1 = mask.toformat(format1)
            ...     for format2 in MaskFormat.cannonical:
            ...         mask2 = mask1.toformat(format2)
            ...         img1 = mask1.to_c_mask().data
            ...         img2 = mask2.to_c_mask().data
            ...         if not np.all(img1 == img2):
            ...             msg = 'Failed convert {} <-> {}'.format(format1, format2)
            ...             print(msg)
            ...             raise AssertionError(msg)
            ...         else:
            ...             msg = 'Passed convert {} <-> {}'.format(format1, format2)
            ...             print(msg)
        """
        key = MaskFormat.aliases.get(format, format)
        try:
            func = self.convert_funcs[key]
            return func(self, copy)
        except KeyError:
            raise KeyError('Cannot convert {} to {}'.format(self.format, format))

    @_register_convertor(MaskFormat.BYTES_RLE)
    def to_bytes_rle(self, copy=False):
        """
        Example:
            >>> from kwimage.structs.mask import MaskFormat  # NOQA
            >>> mask = Mask.random(shape=(8, 8), rng=0)
            >>> print(mask.to_bytes_rle().data['counts'])
            ...'135000k0NWO0L'
            >>> print(mask.to_array_rle().data['counts'].tolist())
            [1, 3, 5, 3, 5, 3, 32, 1, 7, 1, 3]
            >>> print(mask.to_array_rle().to_bytes_rle().data['counts'])
            ...'135000k0NWO0L'
        """
        if self.format == MaskFormat.BYTES_RLE:
            return self.copy() if copy else self
        if self.format == MaskFormat.ARRAY_RLE:
            w, h = self.data['size']
            if self.data.get('order', 'F') != 'F':
                raise ValueError('Expected column-major array RLE')
            newdata = cython_mask.frUncompressedRLE([self.data], h, w)[0]
            self = Mask(newdata, MaskFormat.BYTES_RLE)

        elif self.format == MaskFormat.F_MASK:
            f_masks = self.data[:, :, None]
            encoded = cython_mask.encode(f_masks)[0]
            self = Mask(encoded, format=MaskFormat.BYTES_RLE)
        elif self.format == MaskFormat.C_MASK:
            c_mask = self.data
            f_masks = np.asfortranarray(c_mask)[:, :, None]
            encoded = cython_mask.encode(f_masks)[0]
            self = Mask(encoded, format=MaskFormat.BYTES_RLE)
        else:
            raise NotImplementedError(self.format)
        return self

    @_register_convertor(MaskFormat.ARRAY_RLE)
    def to_array_rle(self, copy=False):
        if self.format == MaskFormat.ARRAY_RLE:
            return self.copy() if copy else self
        elif self.format == MaskFormat.BYTES_RLE:
            # NOTE: inefficient, could be improved
            arr_counts = cython_mask._rle_bytes_to_array(self.data['counts'])
            encoded = {
                'size': self.data['size'],
                'binary': self.data.get('binary', True),
                'counts': arr_counts,
                'order': self.data.get('order', 'F'),
            }
            encoded['shape'] = self.data.get('shape', encoded['size'][::-1])
            self = Mask(encoded, format=MaskFormat.ARRAY_RLE)
        else:
            import kwimage
            f_mask = self.to_fortran_mask().data
            encoded = kwimage.encode_run_length(f_mask, binary=True, order='F')
            encoded['size'] = encoded['shape'][0:2][::-1]  # hack in size
            self = Mask(encoded, format=MaskFormat.ARRAY_RLE)
        return self

    @_register_convertor(MaskFormat.F_MASK)
    def to_fortran_mask(self, copy=False):
        if self.format == MaskFormat.F_MASK:
            return self.copy() if copy else self
        elif self.format == MaskFormat.C_MASK:
            c_mask = self.data.copy() if copy else self.data
            f_mask = np.asfortranarray(c_mask)
        elif self.format == MaskFormat.ARRAY_RLE:
            import kwimage
            encoded = dict(self.data)
            encoded.pop('size', None)
            f_mask = kwimage.decode_run_length(**encoded)
        else:
            # NOTE: inefficient, could be improved
            self = self.to_bytes_rle(copy=False)
            f_mask = cython_mask.decode([self.data])[:, :, 0]
        self = Mask(f_mask, MaskFormat.F_MASK)
        return self

    @_register_convertor(MaskFormat.C_MASK)
    def to_c_mask(self, copy=False):
        if self.format == MaskFormat.C_MASK:
            return self.copy() if copy else self
        elif self.format == MaskFormat.F_MASK:
            f_mask = self.data.copy() if copy else self.data
            c_mask = np.ascontiguousarray(f_mask)
        else:
            f_mask = self.to_fortran_mask(copy=False).data
            c_mask = np.ascontiguousarray(f_mask)
        self = Mask(c_mask, MaskFormat.C_MASK)
        return self


class _MaskConstructorMixin(object):
    """
    Alternative ways to construct a masks object
    """

    @classmethod
    def from_polygons(Mask, polygons, shape):
        """
        Args:
            polygons (ndarray | List[ndarray]): one or more polygons that
                will be joined together. The ndarray may either be an
                Nx2 or a flat c-contiguous array or xy points.
            shape (Tuple): height / width of the source image

        Example:
            >>> polygons = [
            >>>     np.array([[3, 0],[2, 1],[2, 4],[4, 4],[4, 3],[7, 0]]),
            >>>     np.array([[0, 9],[4, 8],[2, 3]]),
            >>> ]
            >>> shape = (9, 5)
            >>> self = Mask.from_polygons(polygons, shape)
            >>> print(self)
            <Mask({'counts': b'724;MG2MN16', 'size': [9, 5]}, format=bytes_rle)>
            >>> polygon = polygons[0]
            >>> print(Mask.from_polygons(polygon, shape))
            <Mask({'counts': b'b04500N2', 'size': [9, 5]}, format=bytes_rle)>
        """
        h, w = shape
        # TODO: holes? geojson?
        if isinstance(polygons, np.ndarray):
            polygons = [polygons]
        flat_polys = [ps.ravel() for ps in polygons]
        encoded = cython_mask.frPoly(flat_polys, h, w)
        ccs = [Mask(e, MaskFormat.BYTES_RLE) for e in encoded]
        self = Mask.union(*ccs)
        return self

    @classmethod
    def from_mask(Mask, mask, offset=None, shape=None, method='faster'):
        """
        Creates an RLE encoded mask from a raw binary mask, but you may
        optionally specify an offset if the mask is part of a larger image.

        Args:
            mask (ndarray):
                a binary submask which belongs to a larger image

            offset (Tuple[int, int]):
                top-left xy location of the mask in the larger image

            shape (Tuple[int, int]): shape of the larger image

        SeeAlso:
            ../../test/test_rle.py

        Example:
            >>> mask = Mask.random(shape=(32, 32), rng=0).data
            >>> offset = (30, 100)
            >>> shape = (501, 502)
            >>> self = Mask.from_mask(mask, offset=offset, shape=shape, method='faster')
        """
        if shape is None:
            shape = mask.shape
        if offset is None:
            offset = (0, 0)
        if method == 'naive':
            # inefficent but used to test correctness of algorithms
            import kwimage
            rc_offset = offset[::-1]
            larger = kwimage.subpixel_translate(mask, rc_offset,
                                                output_shape=shape)
            # larger = np.zeros(shape, dtype=mask.dtype)
            # larger_rc = offset[::-1]
            # mask_dims = mask.shape[0:2]
            # index = tuple(slice(s, s + d) for s, d in zip(larger_rc, mask_dims))
            # larger[index] = mask
            self = Mask(larger, MaskFormat.C_MASK).to_array_rle()
        elif method == 'faster':
            import kwimage
            encoded = kwimage.encode_run_length(mask, binary=True, order='F')
            encoded['size'] = encoded['shape']
            self = Mask(encoded, MaskFormat.ARRAY_RLE)
            self = self.translate(offset, shape)
        else:
            raise KeyError(method)
        return self


class _MaskTransformMixin(object):
    def warp(self):
        raise NotImplementedError

    def translate(self, offset, shape):
        """
        Efficiently translate an array_rle in the encoding space

        Args:
            offset (Tuple): x,y offset
            shape (Tuple): h,w of transformed mask

        Doctest:
            >>> # test that translate works on all zero images
            >>> self = Mask(np.zeros((7, 8), dtype=np.uint8), 'c_mask')
            >>> result = self.translate((1, 2), (6, 9))
            >>> assert np.all(result.data['counts'] == [54])

        Example:
            >>> self = Mask.random(shape=(8, 8), rng=0)
            >>> self.data[6, 0] = 1
            >>> self.data[7, 0] = 1
            >>> self.data[0, 1] = 1

            >>> self.data[7, 3] = 1
            >>> self.data[0, 4] = 1
            >>> self.data[7, 4] = 1

            >>> self.data[4, 6] = 0
            >>> self.data[4, 7] = 0
            >>> print(self.data)
            >>> shape = (10, 10)
            >>> offset = (1, 1)
            >>> self.translate(offset, shape).to_c_mask().data

            img = np.array([
                [1, 1, 1, 1],
                [0, 0, 0, 0],
                [0, 0, 0, 0],
                [1, 1, 1, 1],], dtype=np.uint8)
            self = Mask(img, 'c_mask')

            rle = kwimage.encode_run_length(img, binary=True)
            shape = (7, 7)
            offset = (-2, -2)
            self.translate(offset, shape).to_c_mask().data
        """
        rle = self.to_array_rle(copy=False).data

        if not rle['binary']:
            raise ValueError('rle must be binary')

        # These are the flat indices where the value changes:
        #  * even locs are stop-indices for zeros and start indices for ones
        #  * odd locs are stop-indices for ones and start indices for zeros
        indices = rle['counts'].cumsum()

        if len(indices) % 2 == 1:
            indices = indices[:-1]

        # Transform indices to be start-stop inclusive indices for ones
        indices[1::2] -= 1

        # Find yx points where the binary mask changes value
        old_shape = np.array(rle['shape'])
        new_shape = np.array(shape)
        rc_offset = np.array(offset[::-1])

        pts = np.unravel_index(indices, old_shape, order=rle['order'])
        major_axis = 1 if rle['order'] == 'F' else 0
        minor_axis = 1 - major_axis

        major_idxs = pts[major_axis]

        # Find locations in the major axis points where a non-zero count
        # crosses into the next minor dimension.
        pair_major_index = major_idxs.reshape(-1, 2)
        num_major_crossings = pair_major_index.T[1] - pair_major_index.T[0]
        flat_cross_idxs = np.where(num_major_crossings > 0)[0] * 2

        # Insert breaks to runs in locations that cross the major-axis.
        # This will force all runs to exist only within a single major-axis.
        broken_pts = [x.tolist() for x in pts]
        for idx in flat_cross_idxs[::-1]:
            prev_pt = [x[idx] for x in broken_pts]
            next_pt = [x[idx + 1] for x in broken_pts]
            for break_d in reversed(range(prev_pt[major_axis], next_pt[major_axis])):
                # Insert a breakpoint over every major axis crossing
                if major_axis == 1:
                    new_stop = [old_shape[0] - 1, break_d]
                    new_start = [0, break_d + 1]
                elif major_axis == 0:
                    new_stop = [break_d, old_shape[1] - 1]
                    new_start = [break_d + 1, 0]
                else:
                    raise AssertionError(major_axis)
                broken_pts[0].insert(idx + 1, new_start[0])
                broken_pts[1].insert(idx + 1, new_start[1])
                broken_pts[0].insert(idx + 1, new_stop[0])
                broken_pts[1].insert(idx + 1, new_stop[1])

        # Now that new start-stop locations have been added,
        # translate the points that indices where non-zero data should go.
        new_pts = np.array(broken_pts, dtype=np.int) + rc_offset[:, None]

        # <handle_out_of_bounds>
        # Note: all of the following logic relies on the fact that each run can
        # only span one major axis. This condition is true because we inserted
        # breakpoints whenever a run spanned more than one major axis.

        new_major_dim = new_shape[major_axis]
        new_minor_dim = new_shape[minor_axis]

        # Only keep points where the major axis is in bounds
        _new_major_pts = new_pts[major_axis]
        is_major_ib = ((_new_major_pts >= 0) &
                       (_new_major_pts < new_major_dim))
        # assert np.all(is_major_ib[0::2] == is_major_ib[1::2]), (
        #     'all pairs should be both in-bounds or both out-of-bounds')
        new_pts = new_pts.T[is_major_ib].T
        new_pts = np.ascontiguousarray(new_pts)

        # Now remove any points where the minor axis is OOB in the same
        # direction. (i.e. remove pairs of points that are both left-oob or
        # both right-oob, but dont remove pairs where only one is left-oob or
        # right-oob, because these still create structure in our new image.)
        _new_minor_pts = new_pts[minor_axis]
        is_left_oob = (_new_minor_pts < 0)
        is_right_oob = (_new_minor_pts >= new_minor_dim)
        is_pair_left_oob = (is_left_oob[0::2] & is_left_oob[1::2])
        is_pair_right_oob = (is_right_oob[0::2] & is_right_oob[1::2])
        is_pair_removable = (is_pair_left_oob | is_pair_right_oob)
        new_pts_pairs = new_pts.T.reshape(-1, 2, 2)
        new_pts = new_pts_pairs[~is_pair_removable].reshape(-1, 2).T
        new_pts = np.ascontiguousarray(new_pts)

        # Finally, all new points are strictly within the existing major dims
        # and we have removed any point pair where both pairs were oob in the
        # same direction, we can simply clip any regions along the minor axis
        # that go out of bounds.
        _new_minor_pts = new_pts[minor_axis]
        _new_minor_pts.clip(0, new_minor_dim - 1, out=_new_minor_pts)
        # </handle_out_of_bounds>

        # Now we have translated flat-indices in the new canvas shape
        new_indices = np.ravel_multi_index(new_pts, new_shape, order=rle['order'])
        new_indices[1::2] += 1

        count_dtype = np.int  # use in to eventually support non-binary RLE
        new_indices = new_indices.astype(count_dtype)

        total = int(np.prod(new_shape))
        if len(new_indices) == 0:
            trailing_counts = np.array([total], dtype=count_dtype)
            leading_counts = np.array([], dtype=count_dtype)
        else:
            leading_counts = np.array([new_indices[0]], dtype=count_dtype)
            trailing_counts = np.array([total - new_indices[-1]], dtype=count_dtype)

        body_counts = np.diff(new_indices)
        new_counts = np.hstack([leading_counts, body_counts, trailing_counts])

        new_shape = tuple(new_shape.tolist())
        new_rle = {
            'shape': new_shape,
            'size': new_shape[::-1],
            'order': rle['order'],
            'counts': new_counts,
            'binary': rle['binary'],
        }
        # if False:
        #     offset
        #     print(self.data)
        #     # minor_axis = 1 - major_axis
        #     np.vstack(pts[::-1]).T
        #     # major_d = shape[major_axis]
        #     import kwimage
        #     decoded = kwimage.decode_run_length(
        #         **ub.dict_isect(new_rle, set(new_rle) - {'size'}))
        #     print(decoded)

        new_self = Mask(new_rle, MaskFormat.ARRAY_RLE)
        return new_self


class _MaskDrawMixin(object):
    """
    Non-core functions for mask visualization
    """

    def draw_on(self, image, color='blue', alpha=0.5):
        """
        Draws the mask on an image

        Example:
            >>> from kwimage.structs.mask import *  # NOQA
            >>> import kwimage
            >>> image = kwimage.grab_test_image()
            >>> self = Mask.random(shape=image.shape[0:2])
            >>> toshow = self.draw_on(image)
            >>> # xdoc: +REQUIRES(--show)
            >>> import kwplot
            >>> kwplot.autompl()
            >>> kwplot.imshow(toshow)
            >>> kwplot.show_if_requested()
        """
        import kwplot
        import kwimage

        mask = self.to_c_mask().data
        rgb01 = list(kwplot.Color(color).as01())
        rgba01 = np.array(rgb01 + [1])[None, None, :]
        alpha_mask = rgba01 * mask[:, :, None]
        alpha_mask[..., 3] = mask * alpha

        toshow = kwimage.overlay_alpha_images(alpha_mask, image)
        return toshow

    def draw(self, color='blue', alpha=0.5, ax=None):
        """
        Draw on the current matplotlib axis
        """
        import kwplot
        if ax is None:
            from matplotlib import pyplot as plt
            ax = plt.gca()

        mask = self.to_c_mask().data
        rgb01 = list(kwplot.Color(color).as01())
        rgba01 = np.array(rgb01 + [1])[None, None, :]
        alpha_mask = rgba01 * mask[:, :, None]
        alpha_mask[..., 3] = mask * alpha
        ax.imshow(alpha_mask)


class Mask(ub.NiceRepr, _MaskConversionMixin, _MaskConstructorMixin,
           _MaskTransformMixin, _MaskDrawMixin):
    """
    Manages a single segmentation mask and can convert to and from
    multiple formats including:

        * bytes_rle
        * array_rle
        * c_mask
        * f_mask

    Example:
        >>> # a ms-coco style compressed bytes rle segmentation
        >>> segmentation = {'size': [5, 9], 'counts': ';?1B10O30O4'}
        >>> mask = Mask(segmentation, 'bytes_rle')
        >>> # convert to binary numpy representation
        >>> binary_mask = mask.to_c_mask().data
        >>> print(ub.repr2(binary_mask.tolist(), nl=1, nobr=1))
        [0, 0, 0, 1, 1, 1, 1, 1, 0],
        [0, 0, 1, 1, 1, 0, 0, 0, 0],
        [0, 0, 1, 1, 1, 1, 1, 1, 0],
        [0, 0, 1, 1, 1, 0, 1, 1, 0],
        [0, 0, 1, 1, 1, 0, 1, 1, 0],

    """
    def __init__(self, data=None, format=None):
        self.data = data
        self.format = format

    def __nice__(self):
        return '{}, format={}'.format(ub.repr2(self.data, nl=0), self.format)

    @classmethod
    def random(Mask, rng=None, shape=(32, 32)):
        import kwarray
        import kwimage
        rng = kwarray.ensure_rng(rng)
        # Use random heatmap to make some blobs for the mask
        probs = kwimage.Heatmap.random(
            dims=shape, rng=rng, classes=2).data['class_probs'][1]
        c_mask = (probs > .5).astype(np.uint8)
        self = Mask(c_mask, MaskFormat.C_MASK)
        return self

    def copy(self):
        """
        Performs a deep copy of the mask data

        Example:
            >>> self = Mask.random(shape=(8, 8), rng=0)
            >>> other = self.copy()
            >>> assert other.data is not self.data
        """
        return Mask(copy.deepcopy(self.data), self.format)

    def union(self, *others):
        """
        This can be used as a staticmethod or an instancemethod

        Example:
            >>> masks = [Mask.random(shape=(8, 8), rng=i) for i in range(2)]
            >>> mask = Mask.union(*masks)
            >>> print(mask.area)
            33
        """
        cls = self.__class__ if isinstance(self, Mask) else Mask
        rle_datas = [item.to_bytes_rle().data for item in it.chain([self], others)]
        return cls(cython_mask.merge(rle_datas, intersect=0), MaskFormat.BYTES_RLE)

    def intersection(self, *others):
        """
        This can be used as a staticmethod or an instancemethod

        Example:
            >>> masks = [Mask.random(shape=(8, 8), rng=i) for i in range(2)]
            >>> mask = Mask.intersection(*masks)
            >>> print(mask.area)
            9
        """
        cls = self.__class__ if isinstance(self, Mask) else Mask
        rle_datas = [item.to_bytes_rle().data for item in it.chain([self], others)]
        return cls(cython_mask.merge(rle_datas, intersect=1), MaskFormat.BYTES_RLE)

    @property
    def shape(self):
        if self.format in {MaskFormat.BYTES_RLE, MaskFormat.ARRAY_RLE}:
            if 'shape' in self.data:
                return self.data['shape']
            else:
                return self.data['size'][::-1]
        if self.format in {MaskFormat.C_MASK, MaskFormat.F_MASK}:
            return self.data.shape

    @property
    def area(self):
        """
        Returns the number of non-zero pixels

        Example:
            >>> self = Mask.random(shape=(8, 8), rng=0)
            >>> self.area
            11
        """
        self = self.to_bytes_rle()
        return cython_mask.area([self.data])[0]

    def get_xywh(self):
        """
        Gets the bounding xywh box coordinates of this mask

        Returns:
            ndarray: x, y, w, h: Note we dont use a Boxes object because
                a general singular version does not yet exist.

        Example:
            >>> self = Mask.random(shape=(8, 8), rng=0)
            >>> self.get_xywh().tolist()
            [0.0, 1.0, 8.0, 4.0]
        """
        # import kwimage
        self = self.to_bytes_rle()
        xywh = cython_mask.toBbox([self.data])[0]
        # boxes = kwimage.Boxes(xywh, 'xywh')
        # return boxes
        return xywh

    def get_polygon(self):
        """
        Returns a list of (x,y)-coordinate lists. The length of the list is
        equal to the number of disjoint regions in the mask.

        Returns:
            List[ndarray]: polygon around each connected component of the
                mask. Each ndarray is an Nx2 array of xy points.

        NOTE:
            The returned polygon may not surround points that are only one
            pixel thick.

        Example:
            >>> self = Mask.random(shape=(8, 8), rng=0)
            >>> polygons = self.get_polygon()
            >>> print('polygons = ' + ub.repr2(polygons))
            polygons = [
                np.array([[6, 4],[7, 4]], dtype=np.int32),
                np.array([[0, 1],[0, 3],[2, 3],[2, 1]], dtype=np.int32),
            ]
            >>> polygons = self.get_polygon()
            >>> other = Mask.from_polygons(polygons, self.shape)
            >>> self = self.to_bytes_rle()
            >>> # xdoc: +REQUIRES(--show)
            >>> import kwplot
            >>> kwplot.autompl()
            >>> image = np.ones(self.shape)
            >>> image = self.draw_on(image, color='blue')
            >>> image = other.draw_on(image, color='red')
            >>> kwplot.imshow(image)
        """
        mask = self.to_c_mask().data
        padded_mask = cv2.copyMakeBorder(mask, 1, 1, 1, 1,
                                         cv2.BORDER_CONSTANT, value=0)
        mode = cv2.RETR_LIST
        # mode = cv2.RETR_EXTERNAL

        # https://docs.opencv.org/3.1.0/d3/dc0/
        # group__imgproc__shape.html#ga4303f45752694956374734a03c54d5ff

        method = cv2.CHAIN_APPROX_SIMPLE
        # method = cv2.CHAIN_APPROX_NONE
        # method = cv2.CHAIN_APPROX_TC89_KCOS
        contours_, hierarchy_ = cv2.findContours(padded_mask, mode, method,
                                                 offset=(-1, -1))
        polygon = [c[:, 0, :] for c in contours_]

        # TODO: a kwimage structure for polygons

        if False:
            import kwil
            kwil.autompl()
            # Note that cv2 draw contours doesnt have the 1-pixel thick problem
            # it seems to just be the way the coco implementation is
            # interpreting polygons.
            image = kwil.atleast_3channels(mask)
            toshow = np.zeros(image.shape, dtype="uint8")
            cv2.drawContours(toshow, contours_, -1, (255, 0, 0), 1)
            kwil.imshow(toshow)

        return polygon

    def get_convex_hull(self):
        """
        Returns a list of xy points around the convex hull of this mask

        NOTE:
            The returned polygon may not surround points that are only one
            pixel thick.

        Example:
            >>> self = Mask.random(shape=(8, 8), rng=0)
            >>> polygons = self.get_convex_hull()
            >>> print('polygons = ' + ub.repr2(polygons))
            >>> other = Mask.from_polygons(polygons, self.shape)
        """
        mask = self.to_c_mask().data
        cc_y, cc_x = np.where(mask)
        points = np.vstack([cc_x, cc_y]).T
        hull = cv2.convexHull(points)[:, 0, :]
        return hull

    def iou(self, other):
        """
        The area of intersection over the area of union

        TODO:
            - [ ] Write plural Masks version of this class, which should
                  be able to perform this operation more efficiently.

        Example:
            >>> self = Mask.random(rng=0)
            >>> other = Mask.random(rng=1)
            >>> iou = self.iou(other)
            >>> print('iou = {:.4f}'.format(iou))
            iou = 0.0542
        """
        item1 = self.to_bytes_rle(copy=False).data
        item2 = other.to_bytes_rle(copy=False).data
        # I'm not sure what passing `pyiscrowd` actually does here
        # TODO: determine what `pyiscrowd` does, and document it.
        pyiscrowd = np.array([0], dtype=np.uint8)
        iou = cython_mask.iou([item1], [item2], pyiscrowd)[0, 0]
        return iou


def _rle_bytes_to_array(s):
    """
    python version of an optimized cython implementation

    Ignore:
        from kwimage.structs.mask import _rle_bytes_to_array, cv2, copy, np, ub, it, cython_mask
        s = b';?1B10O30O4'

        import ubelt as ub
        ti = ub.Timerit(1000, bestof=50, verbose=2)
        for timer in ti.reset('python'):
            with timer:
                _rle_bytes_to_array(s)

        for timer in ti.reset('cython'):
            with timer:
                cython_mask._rle_bytes_to_array(s)
    """
    # verbatim inefficient impl.
    # It would be nice if this (un/)compression algo could get a better
    # description.
    import numpy as np
    cnts = np.empty(len(s), dtype=np.int64)
    p = 0
    m = 0
    for m in range(len(s)):
        if p >= len(s):
            break
        x = 0
        k = 0
        more = 1
        while more:
            c = s[p] - 48
            x |= (c & 0x1f) << 5 * k
            more = c & 0x20
            p += 1
            k += 1
            if more == 0 and (c & 0x10):
                x |= (-1 << 5 * k)
        if m > 2:
            x += cnts[m - 2]
        cnts[m] = x
    cnts = cnts[:m]
    return cnts


if __name__ == '__main__':
    """
    CommandLine:
        xdoctest -m kwimage.structs.mask all
    """
    import xdoctest
    xdoctest.doctest_module(__file__)
