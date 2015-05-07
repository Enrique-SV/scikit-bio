# ----------------------------------------------------------------------------
# Copyright (c) 2013--, scikit-bio development team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
# ----------------------------------------------------------------------------

from __future__ import absolute_import, division, print_function
from future.builtins import range
from future.utils import viewitems
from future.standard_library import hooks
from six import string_types, text_type

import re
import collections
import numbers
from contextlib import contextmanager

import numpy as np
from scipy.spatial.distance import hamming

from skbio._base import SkbioObject
from skbio.util._misc import reprnator

with hooks():
    from itertools import zip_longest


class Sequence(collections.Sequence, SkbioObject):
    """Store biological sequence data and optional associated metadata.

    ``Sequence`` objects do not enforce an alphabet and are thus the most
    generic objects for storing biological sequence data. Subclasses ``DNA``,
    ``RNA``, and ``Protein`` enforce the IUPAC character set [1]_ and provide
    operations specific to each respective molecule type.

    Parameters
    ----------
    sequence : str, Sequence, or 1D np.ndarray (np.uint8 or '\|S1')
        Characters representing the biological sequence itself.
    id : str, optional
        Sequence identifier (e.g., an accession number).
    description : str, optional
        Description or comment about the sequence (e.g., "green fluorescent
        protein").
    quality : 1D array_like (int), optional
        Phred quality scores stored as nonnegative integers, one per sequence
        character. If provided, must be the same length as the biological
        sequence. Can be a 1D ``np.ndarray`` of integers or a structure that
        can be converted into this representation using ``np.asarray``. A copy
        will *not* be made if `quality` is already a 1D ``np.ndarray``
        with an ``int`` ``dtype``. The array will be made read-only (i.e., its
        ``WRITEABLE`` flag will be set to ``False``).

    Attributes
    ----------
    sequence
    id
    description
    quality

    See Also
    --------
    DNA
    RNA
    Protein

    Notes
    -----
    ``Sequence`` objects are immutable.

    References
    ----------
    .. [1] Nomenclature for incompletely specified bases in nucleic acid
       sequences: recommendations 1984.
       Nucleic Acids Res. May 10, 1985; 13(9): 3021-3030.
       A Cornish-Bowden

    Examples
    --------
    >>> from skbio import Sequence
    >>> s = Sequence('GGUCGUGAAGGA')
    >>> t = Sequence('GGUCCUGAAGGU')
    >>> s == t
    False

    """
    default_write_format = 'fasta'
    __hash__ = None  # TODO revisit hashability when all properties are present

    @property
    def sequence(self):
        """Array containing underlying biological sequence characters.

        Notes
        -----
        This property is not writeable.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('AACGA', id='seq1', description='some seq')
        >>> s.sequence # doctest: +NORMALIZE_WHITESPACE
        array(['A', 'A', 'C', 'G', 'A'],
              dtype='|S1')

        """
        return self._bytes.view('|S1')

    @property
    def id(self):
        """Biological sequence identifier.

        Notes
        -----
        This property is not writeable.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUCGUAAAGGA', id='seq1', description='some seq')
        >>> s.id
        'seq1'

        """
        return self._id

    @property
    def description(self):
        """Biological sequence description.

        Notes
        -----
        This property is not writeable.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUCGUAAAGGA', id='seq1', description='some seq')
        >>> s.description
        'some seq'

        """
        return self._description

    @property
    def quality(self):
        """Quality scores corresponding to biological sequence characters.

        A 1D ``np.ndarray`` of nonnegative integers representing a Phred
        quality score for each character in the biological sequence, or
        ``None`` if quality scores are not present.

        Notes
        -----
        This property is not writeable. A copy of the array is *not* returned.
        The array is read-only (i.e., its ``WRITEABLE`` flag is set to
        ``False``).

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUCGUGACCGA', id='seq1', description='some seq',
        ...              quality=[1, 5, 3, 3, 2, 42, 100, 9, 10, 0, 42, 42])
        >>> s.quality
        array([  1,   5,   3,   3,   2,  42, 100,   9,  10,   0,  42,  42])

        """
        return self._quality

    @property
    def _string(self):
        return self._bytes.tostring()

    def __init__(self, sequence, id="", description="", quality=None):
        if isinstance(sequence, Sequence):
            if id == "":
                id = sequence.id
            if description == "":
                description = sequence.description
            if quality is None:
                quality = sequence.quality

            sequence = sequence._bytes

        self._set_id(id)
        self._set_description(description)
        self._set_sequence(sequence)
        self._set_quality(quality)

    def _set_id(self, id_):
        if isinstance(id_, str):
            self._id = id_
        else:
            raise TypeError("Sequence ID %r must be of type `str`." % (id_,))

    def _set_description(self, description):
        if isinstance(description, str):
            self._description = description
        else:
            raise TypeError("Sequence description %r must be of type `str`." %
                            (description,))

    def _set_sequence(self, sequence):
        """Munge the sequence data into a numpy array."""
        is_ndarray = isinstance(sequence, np.ndarray)
        if is_ndarray:
            if np.issubdtype(sequence.dtype, np.uint8):
                pass
            elif sequence.dtype == '|S1':
                sequence = sequence.view(np.uint8)
            else:
                raise TypeError(
                    "Can only create sequence from numpy.ndarray of dtype "
                    "np.uint8 or '|S1'. Invalid dtype: %s" %
                    sequence.dtype)

            # numpy doesn't support views of non-contiguous arrays. Since we're
            # making heavy use of views internally, and users may also supply
            # us with a view, make sure we *always* store a contiguous array to
            # avoid hard-to-track bugs. See
            # https://github.com/numpy/numpy/issues/5716
            potential_copy = np.ascontiguousarray(sequence)
            if potential_copy is not sequence:
                self._owns_bytes = True
            else:
                self._owns_bytes = False
            sequence = potential_copy
        else:
            # Python 3 will not raise a UnicodeEncodeError so we force it by
            # encoding it as ascii
            if isinstance(sequence, text_type):
                sequence = sequence.encode("ascii")
            s = np.fromstring(sequence, dtype=np.uint8)

            # There are two possibilities (to our knowledge) at this point:
            # Either the sequence we were given was something string-like,
            # (else it would not have made it past fromstring), or it was a
            # numpy scalar, and so our length must be 1.
            if isinstance(sequence, np.generic) and len(s) != 1:
                raise TypeError("Can cannot create a sequence with %r" %
                                type(sequence).__name__)

            sequence = s
            self._owns_bytes = True

        sequence.flags.writeable = False
        self._bytes = sequence

    def _set_quality(self, quality):
        if quality is not None:
            quality = np.asarray(quality)

            if quality.ndim == 0:
                # We have something scalar-like, so create a single-element
                # vector to store it.
                quality = np.reshape(quality, 1)

            if quality.shape == (0,):
                # cannot safe cast an empty vector from float to int
                cast_type = 'unsafe'
            else:
                cast_type = 'safe'

            quality = quality.astype(int, casting=cast_type, copy=False)
            quality.flags.writeable = False

            if quality.ndim != 1:
                raise ValueError(
                    "Quality scores have %d dimension(s). Quality scores must "
                    "be 1-D." % quality.ndim)

            if len(quality) != len(self):
                raise ValueError(
                    "Number of quality scores (%d) must match the "
                    "number of characters in the sequence (%d)." %
                    (len(quality), len(self)))

            if (quality < 0).any():
                raise ValueError(
                    "Quality scores must be greater than or equal to "
                    "zero.")

        self._quality = quality

    def __contains__(self, subsequence):
        """Determine if a subsequence is contained in the biological sequence.

        Parameters
        ----------
        subsequence : str, Sequence, or 1D np.ndarray (np.uint8 or '\|S1')
            The putative subsequence.

        Returns
        -------
        bool
            Indicates whether `subsequence` is contained in the biological
            sequence.

        Raises
        ------
        TypeError
            If `subsequence` is a ``Sequence`` object with a different type
            than the biological sequence.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUCGUGAAGGA')
        >>> 'GGU' in s
        True
        >>> 'CCC' in s
        False

        """
        subsequence = self._munge_to_sequence(subsequence, "in")
        return subsequence._string in self._string

    def __eq__(self, other):
        """Determine if the biological sequence is equal to another.

        Biological sequences are equal if they are *exactly* the same type and
        their sequence characters, ID, description, and quality scores are the
        same.

        Parameters
        ----------
        other : Sequence
            Sequence to test for equality against.

        Returns
        -------
        bool
            Indicates whether the biological sequence is equal to `other`.

        See Also
        --------
        equals

        Notes
        -----
        See ``Sequence.equals`` for more control over equality testing.

        This method is equivalent to ``self.equals(other)``.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUCGUGAAGGA')
        >>> t = Sequence('GGUCGUGAAGGA')
        >>> s == t
        True
        >>> u = Sequence('GGUCGUGACCGA')
        >>> u == t
        False

        Note that because the quality scores do not match between ``u`` and
        ``v``, they are not considered equal:

        >>> v = Sequence('GGUCGUGACCGA',
        ...              quality=[1, 5, 3, 3, 2, 42, 100, 9, 10, 0, 42, 42])
        >>> u == v
        False

        """
        return self.equals(other)

    def __ne__(self, other):
        """Determine if the biological sequence is not equal to another.

        Biological sequences are not equal if they are not *exactly* the same
        type, or their sequence characters, ID, description, or quality scores
        differ.

        Parameters
        ----------
        other : Sequence
            Sequence to test for inequality against.

        Returns
        -------
        bool
            Indicates whether the biological sequence is not equal to `other`.

        See Also
        --------
        equals

        Notes
        -----
        See ``Sequence.equals`` for more control over equality testing.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUCGUGAAGGA')
        >>> t = Sequence('GGUCGUGAAGGA')
        >>> s != t
        False
        >>> u = Sequence('GGUCGUGACCGA')
        >>> u != t
        True
        >>> v = Sequence('GGUCGUGACCGA', id='v')
        >>> u != v
        True

        """
        return not (self == other)

    def __getitem__(self, indexable):
        """Slice the biological sequence.

        Parameters
        ----------
        indexable : int, slice, iterable (int and slice), 1D array_like (bool)
            The position(s) to return from the biological sequence. If
            `indexable` is an iterable of integers, these are assumed to be
            indices in the sequence to keep. If `indexable` is a 1D
            ``array_like`` of booleans, these are assumed to be the positions
            in the sequence to keep.

        Returns
        -------
        Sequence
            New biological sequence containing the position(s) specified by
            `indexable` in the current biological sequence. If quality scores
            are present, they will be sliced in the same manner and included in
            the returned biological sequence. ID and description are also
            included.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUCGUGAAGGA')

        Obtain a single character from the biological sequence:

        >>> s[1]
        Sequence('G', length=1)

        Obtain a slice:

        >>> s[7:]
        Sequence('AAGGA', length=5)

        Obtain characters at the following indices:

        >>> s[[3, 4, 7, 0, 3]]
        Sequence('CGAGC', length=5)

        Obtain characters at positions evaluating to `True`:

        >>> s = Sequence('GGUCG')
        >>> s[[True, False, True, 'a' is 'a', False]]
        Sequence('GUC', length=3)

        """
        qual = None
        if (not isinstance(indexable, np.ndarray) and
            ((not isinstance(indexable, string_types)) and
             hasattr(indexable, '__iter__'))):
            indexable_ = indexable
            indexable = np.asarray(indexable)

            if indexable.dtype == object:
                indexable = list(indexable_)  # TODO: Don't blow out memory

                if len(indexable) == 0:
                    # indexing with an empty list, so convert to ndarray and
                    # fall through to ndarray slicing below
                    indexable = np.asarray(indexable)
                else:
                    seq = np.concatenate(
                        list(self._slices_from_iter(self._bytes, indexable)))
                    if self._has_quality():
                        qual = np.concatenate(list(self._slices_from_iter(
                            self.quality, indexable)))

                    return self._to(sequence=seq, quality=qual)
        elif isinstance(indexable, string_types) or \
                isinstance(indexable, bool):
            raise IndexError("Cannot index with %s type: %r" %
                             (type(indexable).__name__, indexable))

        if (isinstance(indexable, np.ndarray) and
            indexable.dtype == bool and
                len(indexable) != len(self)):
            raise IndexError("An boolean vector index must be the same length"
                             " as the sequence (%d, not %d)." %
                             (len(self), len(indexable)))

        if isinstance(indexable, np.ndarray) and indexable.size == 0:
            # convert an empty ndarray to a supported dtype for slicing a numpy
            # array
            indexable = indexable.astype(int)

        seq = self._bytes[indexable]
        if self._has_quality():
            qual = self.quality[indexable]

        return self._to(sequence=seq, quality=qual)

    def _slices_from_iter(self, array, indexables):
        for i in indexables:
            if isinstance(i, slice):
                pass
            elif isinstance(i, numbers.Integral) and not isinstance(i, bool):
                if i == -1:
                    i = slice(i, None)
                else:
                    i = slice(i, i+1)
            else:
                raise IndexError("Cannot slice sequence from iterable "
                                 "containing %r." % i)

            yield array[i]

    def __len__(self):
        """Return the number of characters in the biological sequence.

        Returns
        -------
        int
            The length of the biological sequence.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUC')
        >>> len(s)
        4

        """
        return self._bytes.size

    def __iter__(self):
        """Iterate over positions in the biological sequence.

        Returns
        -------
        iterator
            Position iterator for the biological sequence.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUC')
        >>> for c in s:
        ...     c
        Sequence('G', length=1)
        Sequence('G', length=1)
        Sequence('U', length=1)
        Sequence('C', length=1)

        """
        if self._has_quality():
            qual = self.quality
        else:
            qual = []

        for c, q in zip_longest(self.sequence, qual, fillvalue=None):
            yield self._to(sequence=c, quality=q)

    def __reversed__(self):
        """Iterate over positions in the biological sequence.

        Returns
        -------
        iterator
            Reverse position iterator for the biological sequence.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUC')
        >>> for c in reversed(s):
        ...     c
        Sequence('C', length=1)
        Sequence('U', length=1)
        Sequence('G', length=1)
        Sequence('G', length=1)

        """
        return iter(self[::-1])

    def __str__(self):
        """Return biological sequence characters as a string.

        Returns
        -------
        str
            Sequence characters as a string. No metadata (e.g., ID,
            desctiption, or quality scores) will be included.

        See Also
        --------
        sequence

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUCGUAAAGGA', id='hello', description='world')
        >>> str(s)
        'GGUCGUAAAGGA'

        """
        return str(self._string.decode("ascii"))

    def __repr__(self):
        """Return a string representation of the biological sequence object.

        Returns
        -------
        str
            String representation of the biological sequence object.

        Notes
        -----
        String representation contains the class name, the first seven
        characters of the sequence, followed by ellipses, followed by the last
        seven characters of the sequence (or the full sequence without
        ellipses, if the sequence is less than 21 characters long), followed by
        the sequence length. If ID, description, or quality are present, they
        will be included after the sequence length (and truncated in a similar
        manner if they are too long).

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUCGUGAAGGA')
        >>> repr(s)
        "Sequence('GGUCGUGAAGGA', length=12)"
        >>> t = Sequence('ACGT')
        >>> repr(t)
        "Sequence('ACGT', length=4)"
        >>> t
        Sequence('ACGT', length=4)
        >>> Sequence('GGUCGUGAAAAAAAAAAAAGGA')
        Sequence('GGUCGU ... AAAGGA', length=22)
        >>> Sequence('ACGT', id='seq1')
        Sequence('ACGT', length=4, id='seq1')

        """
        start = self.__class__.__name__ + "("
        end = ")"

        tokens = []

        tokens.append(self._format_str(self))
        tokens.append("length=%d" % len(self))
        if self.id:
            tokens.append("id=" + self._format_str(self.id))
        if self.description:
            tokens.append("description=" + self._format_str(self.description))
        if self._has_quality():
            tokens.append("quality=" + self._format_list(self.quality))

        return reprnator(start, tokens, end)

    def _format_str(self, s):
        s = repr(str(s))
        if len(s) > 20:
            return "%s ... %s" % (s[:7], s[-7:])
        return s

    def _format_list(self, l):
        l = list(l)
        if len(l) > 13:
            return "[%s, ..., %s]" % (repr(l[:6])[1:-1], repr(l[-6:])[1:-1])
        return "%r" % l

    def equals(self, other, ignore=None):
        """Determine if the biological sequence is equal to another.

        By default, biological sequences are equal if they are *exactly* the
        same type and their sequence characters, ID, description, and quality
        scores are the same.

        Parameters
        ----------
        other : Sequence
            Sequence to test for equality against.
        ignore : iterable (str), optional
            List of features to ignore in the equality test. By default, all
            features must be the same for two biological sequences to be
            considered equal. Features that can be ignored are ``'type'``,
            ``'id'``, ``'description'``, ``'quality'``, and ``'sequence'``.

        Returns
        -------
        bool
            Indicates whether the biological sequence is equal to `other`.

        Examples
        --------
        Define two biological sequences that have the same underlying sequence
        of characters:

        >>> from skbio import Sequence
        >>> s = Sequence('GGUCGUGAAGGA')
        >>> t = Sequence('GGUCGUGAAGGA')

        The two sequences are considered equal because they are the same type,
        their underlying sequence of characters are the same, and their
        optional attributes (id, description, and quality scores) were not
        provided:

        >>> s.equals(t)
        True
        >>> t.equals(s)
        True

        Define another biological sequence with a different sequence of
        characters than the previous two biological sequences:

        >>> u = Sequence('GGUCGUGACCGA')
        >>> u.equals(t)
        False

        Define a biological sequence with the same sequence of characters as
        ``u``, but with different identifier and quality scores:
        >>> v = Sequence('GGUCGUGACCGA', id='abc',
        ...                        quality=[1, 5, 3, 3, 2, 42, 100, 9, 10, 55,
        ...                                 42, 42])

        By default, the two sequences are *not* considered equal because their
        identifiers and quality scores do not match:

        >>> u.equals(v)
        False

        By specifying that the quality scores and identifier should be ignored,
        they now compare equal:

        >>> u.equals(v, ignore=['quality', 'id'])
        True

        """
        if ignore is None:
            ignore = {}

        # Checks are ordered from least to most expensive.
        if 'type' not in ignore and self.__class__ != other.__class__:
            return False

        if 'id' not in ignore and self.id != other.id:
            return False

        if ('description' not in ignore and
                self.description != other.description):
            return False

        # Use array_equal instead of (a == b).all() because of this issue:
        #     http://stackoverflow.com/a/10582030
        if 'quality' not in ignore and not np.array_equal(self.quality,
                                                          other.quality):
            return False

        if 'sequence' not in ignore and self._string != other._string:
            return False

        return True

    def count(self, subsequence, start=None, end=None):
        """Count occurrences of a subsequence in the biological sequence.

        Parameters
        ----------
        subsequence : str, Sequence, or 1D np.ndarray (np.uint8 or '\|S1')
            Subsequence to count occurrences of.
        start : int, optional
            The position at which to start counting (inclusive).
        end : int, optional
            The position at which to stop counting (exclusive).

        Returns
        -------
        int
            Number of occurrences of `subsequence` in the biological sequence.

        Raises
        ------
        ValueError
            If `subsequence` is of length 0.
        TypeError
            If `subsequence` is a ``Sequence`` object with a different type
            than the biological sequence.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUCG')
        >>> s.count('G')
        3
        >>> s.count('GG')
        1
        >>> s.count('T')
        0

        """
        if len(subsequence) == 0:
            raise ValueError("`count` is not defined for empty subsequences.")

        subsequence = self._munge_to_sequence(subsequence, "count")

        return self._string.count(subsequence._string, start, end)

    def index(self, subsequence, start=None, end=None):
        """Find position where subsequence first occurs in the sequence.

        Parameters
        ----------
        subsequence : str, Sequence, or 1D np.ndarray (np.uint8 or '\|S1')
            Subsequence to search for in the biological sequence.
        start : int, optional
            The position at which to start searching (inclusive).
        end : int, optional
            The position at which to stop searching (exclusive).

        Returns
        -------
        int
            Position where `subsequence` first occurs in the biological
            sequence.

        Raises
        ------
        ValueError
            If `subsequence` is not present in the biological sequence.
        TypeError
            If `subsequence` is a ``Sequence`` object with a different type
            than the biological sequence.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('ACACGACGTT-')
        >>> s.index('ACG')
        2

        """
        try:
            return self._string.index(
                self._munge_to_sequence(subsequence, "index")._string, start,
                end)
        except ValueError:
            raise ValueError(
                "%r is not present in %r." % (subsequence, self))

    def distance(self, other, metric=None):
        """Returns the distance to other

        Parameters
        ----------
        other : `Sequence`
            The `Sequence` to compute the distance to.
        metric : function, optional
            Function used to compute the distance between `self` and `other`.
            If ``None`` (the default), `scipy.spatial.distance.hamming` will be
            used.

        Returns
        -------
        float
            The distance between `self` and `other`.

        Raises
        ------
        skbio.sequence.SequenceError
            If ``len(self) != len(other)`` when ``metric == None`` (i.e.,
            metic is ``scipy.spatial.distance.hamming``). This is only checked
            when using this metric, as equal length is not a requirement
            for all sequence distance metrics. In general, the metric itself
            should test and give an informative error message, but the message
            from ``scipy.spatial.distance.hamming`` is cryptic (as of this
            writing), and it's the default metric, so we explicitly do this
            check here. This metric-specific check will be removed from this
            method when the sequence.stats module is created (track progress on
            this [here](https://github.com/biocore/scikit-bio/issues/913)).

        See Also
        --------
        fraction_diff
        fraction_same
        skbio.DistanceMatrix
        scipy.spatial.distance.hamming

        Examples
        --------
        >>> from skbio.sequence import Sequence
        >>> s = Sequence('GGUC')
        >>> t = Sequence('AGUC')
        >>> s.distance(t)
        0.25
        >>> def dumb_dist(s1, s2): return 0.42
        >>> s.distance(t, dumb_dist)
        0.42

        .. shownumpydoc

        """
        # TODO refactor this method to accept a name (string) of the distance
        # metric to apply and accept **kwargs
        other = self._munge_to_sequence(other, 'distance')
        if metric is None:
            # Hamming requires equal length sequences. We are checking this
            # here because the error you would get otherwise is cryptic.
            if len(self) != len(other):
                raise ValueError(
                    "Sequences do not have equal length. "
                    "Hamming distances can only be computed between "
                    "sequences of equal length.")
            metric = hamming
        return float(metric(self.sequence, other.sequence))

    def matches(self, other):
        """Returns positions that match with other

        Parameters
        ----------
        other : `Sequence`
            The `Sequence` to compare to.

        Returns
        -------
        np.ndarray of bool
            Boolean vector where `True` at position `i` indicates a match
            between the sequences at their positions `i`.

        Raises
        ------
        ValueError
            If ``len(self) != len(other)``.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUC')
        >>> t = Sequence('GAUU')
        >>> s.matches(t)
        array([ True, False,  True, False], dtype=bool)

        .. shownumpydoc

        """
        other = self._munge_to_sequence(other, 'matches/mismatches')
        if len(self) != len(other):
            raise ValueError("Match and mismatch vectors can only be "
                             "generated from equal length sequences.")
        return self._bytes == other._bytes

    def mismatches(self, other):
        """Returns positions that do not match with other

        Parameters
        ----------
        other : `Sequence`
            The `Sequence` to compare to.

        Returns
        -------
        np.ndarray of bool
            Boolean vector where `True` at position `i` indicates a mismatch
            between the sequences at their positions `i`.

        Raises
        ------
        ValueError
            If ``len(self) != len(other)``.

        Examples
        --------
        >>> from skbio import Sequence
        >>> s = Sequence('GGUC')
        >>> t = Sequence('GAUU')
        >>> s.mismatches(t)
        array([False,  True, False,  True], dtype=bool)

        .. shownumpydoc

        """
        return np.invert(self.matches(other))

    def mismatch_frequency(self, other, relative=False):
        """Return count of positions that differ relative to `other`

        Parameters
        ----------
        other : `Sequence`
            The `Sequence` to compare against.
        relative : ``bool``
            If ``True``, return the fraction of mismatches instead of the
            count.

        Returns
        -------
        int, float
            The number of positions that differ between `self` and `other`.
            This will be an ``int`` if ``relative == False``, and a ``float``
            if ``relative == True``.

        Raises
        ------
        skbio.sequence.SequenceError
            If ``len(self) != len(other)``.

        See Also
        --------
        distance
        match_frequency
        mismatches

        Examples
        --------
        >>> from skbio.sequence import Sequence
        >>> s = Sequence('GGUC')
        >>> t = Sequence('AGUC')
        >>> s.mismatch_frequency(t)
        1
        >>> s.mismatch_frequency(t, relative=True)
        0.25

        .. shownumpydoc

        """
        if relative:
            return float(self.mismatches(other).mean())
        else:
            return int(self.mismatches(other).sum())

    def match_frequency(self, other, relative=False):
        """Return count of positions that are the same relative to `other`

        Parameters
        ----------
        other : `Sequence`
            The `Sequence` to compare against.
        relative : `bool`
            If ``True``, return the fraction of matches instead of the count.

        Returns
        -------
        int, float
            The number of positions that are the same between `self` and
            `other`. This will be an ``int`` if ``relative == False``, and a
            ``float`` if ``relative == True``.

        Raises
        ------
        skbio.sequence.SequenceError
            If ``len(self) != len(other)``.

        See Also
        --------
        distance
        mismatch_frequency
        scipy.spatial.distance.hamming

        Examples
        --------
        >>> from skbio.sequence import Sequence
        >>> s = Sequence('GGUC')
        >>> t = Sequence('AGUC')
        >>> s.match_frequency(t)
        3
        >>> s.match_frequency(t, relative=True)
        0.75

        .. shownumpydoc

        """
        if relative:
            return float(self.matches(other).mean())
        else:
            return int(self.matches(other).sum())

    def kmers(self, k, overlap=True):
        """Generator of words of length k

        Parameters
        ----------
        k : int
            The word length.
        overlap : bool, optional
            Defines whether the k-words should be overlapping or not
            overlapping.

        Returns
        -------
        iterator of Sequences
            Iterator of words of length `k` contained in the Sequence.

        Raises
        ------
        ValueError
            If ``k < 1``.

        Examples
        --------
        >>> from skbio.sequence import Sequence
        >>> s = Sequence('ACACGACGTT')
        >>> [str(kw) for kw in s.kmers(4, overlap=False)]
        ['ACAC', 'GACG']
        >>> [str(kw) for kw in s.kmers(3, overlap=True)]
        ['ACA', 'CAC', 'ACG', 'CGA', 'GAC', 'ACG', 'CGT', 'GTT']

        .. shownumpydoc

        """
        if k < 1:
            raise ValueError("k must be greater than 0.")

        step = 1 if overlap else k

        for i in range(0, len(self) - k + 1, step):
            yield self[i:i+k]

    def kmer_frequencies(self, k, overlap=True, relative=False):
        """Get the counts of words of length k

        Parameters
        ----------
        k : int
            The word length.
        overlap : bool, optional
            Defines whether the k-words should be overlapping or not
            overlapping.
        relative : bool
            If ``True``, return the fractional abundance of each kmer instead
            of its count.


        Returns
        -------
        collections.Counter, defaultdict
            The frequencies of words of length `k` contained in the
            Sequence. Will be a ``Counter`` if ``relative == False``, and a
            ``defaultdict`` if ``relative == True``.

        Raises
        ------
        ValueError
            If ``k < 1`` or `k` is longer than the sequence.


        Examples
        --------
        >>> from skbio.sequence import Sequence
        >>> s = Sequence('ACACATTTATTA')
        >>> s.kmer_frequencies(3, overlap=False)
        Counter({'TTA': 2, 'ACA': 1, 'CAT': 1})
        >>> s.kmer_frequencies(3, relative=True, overlap=False)
        defaultdict(<type 'float'>, {'ACA': 0.25, 'TTA': 0.5, 'CAT': 0.25})

        .. shownumpydoc

        """
        kmers = self.kmers(k, overlap=overlap)
        freqs = collections.Counter((str(seq) for seq in kmers))

        if relative:
            if overlap:
                num_kmers = len(self) - k + 1
            else:
                num_kmers = len(self) // k

            relative_freqs = collections.defaultdict(float)
            for kmer, count in viewitems(freqs):
                relative_freqs[kmer] = count / num_kmers
            freqs = relative_freqs

        return freqs

    def slices_from_regex(self, regex, exclude=None):
        """Find patterns specified by regular expression

        Parameters
        ----------
        regex : str, SRE_Pattern
            A string to be compiled into a regular expression, or a pre-
            compiled regular expression (e.g., from re.compile) with
            finditer method.
        exclude : array of bool
            A boolean vector indicating positions to ignore when matching.

        Returns
        -------
        generator
            Yields lists of 3-tuples. Each 3-tuple represents a slice from
            ``self`` (i.e., ``(start, end, step)``) where the regular
            expression matched.

        Example
        -------
        >>> from skbio import DNA
        >>> s = DNA('AATATACCGGTTATAA')
        >>> for e in s.slices_from_regex('(TATA+)'): print (e, s[e])
        slice(2, 6, None) TATA
        slice(11, 16, None) TATAA

        # Gaps can interupt the identification of meaninful patterns
        >>> s = DNA('AATA--TACCGGTTATA-A')
        >>> for e in s.slices_from_regex('(TATA+)'): print (e, s[e])
        slice(13, 17, None) TATA

        # Pass a boolean vector indictating where the gaps are to ignore them.
        >>> for e in s.slices_from_regex('(TATA+)', s.gaps()): print (e, s[e])
        slice(2, 8, None) TA--TA
        slice(13, 19, None) TATA-A

        .. shownumpydoc

        """
        if isinstance(regex, string_types):
            regex = re.compile(regex)

        if exclude is None:
            lookup = np.arange(len(self))
            string = str(self)
        else:
            include = np.invert(exclude)
            lookup = np.where(include)[0]
            string = str(self[include])

        for match in regex.finditer(string):
            # We start at 1 because we don't want the group that contains all
            # other groups.
            for g in range(1, len(match.groups())+1):
                yield slice(lookup[match.start(g)],
                            lookup[match.end(g) - 1] + 1)

    def _has_quality(self):
        """Return bool indicating presence of quality scores in the sequence.

        Returns
        -------
        bool
            ``True`` if the biological sequence has quality scores, ``False``
            otherwise.

        See Also
        --------
        quality

        """
        return self.quality is not None

    def _to(self, **kwargs):
        """Return a copy of the current biological sequence.

        Returns a copy of the current biological sequence, optionally with
        updated attributes specified as keyword arguments.

        Parameters
        ----------
        kwargs : dict, optional
            Keyword arguments passed to the ``Sequence`` (or
            subclass) constructor. The returned copy will have its attributes
            updated based on the values in `kwargs`. If an attribute is
            missing, the copy will keep the same attribute as the current
            biological sequence. Valid attribute names are `'sequence'`,
            `'id'`, `'description'`, and `'quality'`. Default behavior is to
            return a copy of the current biological sequence without changing
            any attributes.

        Returns
        -------
        Sequence
            Copy of the current biological sequence, optionally with updated
            attributes based on `kwargs`. Will be the same type as the current
            biological sequence (`self`).

        Notes
        -----
        This is a shallow copy, but since biological sequences are immutable,
        it is conceptually the same as a deep copy.

        This method is the preferred way of creating new instances from an
        existing biological sequence, instead of calling
        ``self.__class__(...)``, as the latter can be error-prone (e.g.,
        it's easy to forget to propagate attributes to the new instance).

        Examples
        --------
        Create a biological sequence:

        >>> from skbio import Sequence
        >>> seq = Sequence('AACCGGTT', id='id1',
        ...                          description='biological sequence',
        ...                          quality=[4, 2, 22, 23, 1, 1, 1, 9])

        Create a copy of ``seq``, keeping the same underlying sequence of
        characters and quality scores, while updating ID and description:

        >>> new_seq = seq._to(id='new-id', description='new description')

        Note that the copied biological sequence's underlying sequence and
        quality scores are the same as ``seq``:

        >>> str(new_seq)
        'AACCGGTT'
        >>> new_seq.quality
        array([ 4,  2, 22, 23,  1,  1,  1,  9])

        The ID and description have been updated:

        >>> new_seq.id
        'new-id'
        >>> new_seq.description
        'new description'

        The original biological sequence's ID and description have not been
        changed:

        >>> seq.id
        'id1'
        >>> seq.description
        'biological sequence'

        .. shownumpydoc

        """
        defaults = {
            'sequence': self._bytes,
            'id': self.id,
            'description': self.description,
            'quality': self.quality
        }
        defaults.update(kwargs)
        return self._constructor(**defaults)

    def _constructor(self, **kwargs):
        return self.__class__(**kwargs)

    def _munge_to_sequence(self, other, method):
        if isinstance(other, Sequence):
            if type(other) != type(self):
                raise TypeError("Cannot use %s and %s together with `%s`" %
                                (self.__class__.__name__,
                                 other.__class__.__name__, method))
            else:
                return other

        # We don't use self.__class__ or self._constructor here because we want
        # to construct the most general type of Sequence object in order to
        # avoid validation errors.
        return Sequence(other)

    @contextmanager
    def _byte_ownership(self):
        if not self._owns_bytes:
            self._bytes = self._bytes.copy()
            self._owns_bytes = True

        self._bytes.flags.writeable = True
        yield
        self._bytes.flags.writeable = False
