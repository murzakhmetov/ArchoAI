# Concrete-Crack-Detection-Segmentation

This repository contains the code for crack detection on concrete surfaces. It is a PyTorch implementation of Deep Learning-Based Crack Damage Detection Using Convolutional Neural Networks with DeepCrack

DeepCrack: A Deep Hierarchical Feature Learning Architecture for Crack Segmentation


Resources: [(Paper: https://github.com/yhlleo/DeepCrack/blob/master/paper/DeepCrack-Neurocomputing-2019.pdf)]
Architecture: based on Holistically-Nested Edge Detection, ICCV 2015.


Dependencies required:

PyTorch,
OpenCV,
Dataset -The data set can be downloaded from this link: https://data.mendeley.com/datasets/5y9wdsg2zt/2

Dataset:
The dataset contains concrete images having cracks. The data is collected from various METU Campus Buildings.
The dataset is divided into two as negative and positive crack images for image classification. 
Each class has 20000 images with a total of 40000 images with 227 x 227 pixels with RGB channels. 
The dataset is generated from 458 high-resolution images (4032x3024 pixel) with the method proposed by Zhang et al (2016). 
High-resolution images have variance in terms of surface finish and illumination conditions. 
No data augmentation in terms of random rotation or flipping is applied. 

The dataset file creates the training dataset class to be fed into the Convolutional Neural Network. This class automatically determines the number of classes by the number of folders in 'in_dir' (number of folders=number of classes)

![Capture](https://github.com/yhlleo/DeepCrack/blob/master/figures/architecture.jpg?raw=true)

The first type of result is created using the file: Crack recognition.ipynb and the predictions can be seen here:

![Capture](https://user-images.githubusercontent.com/46296774/103016160-edd0b180-4541-11eb-8cfe-3c7680569eb9.PNG)
![Capture2](https://user-images.githubusercontent.com/46296774/103016173-f4f7bf80-4541-11eb-9bb5-933dcd725d9b.PNG)

The second type of prediction is created using the files: cv2_utils.py and inference_utils.py
inference_utils.py is using the Deepcrack model to predict the mask and afterwards the file cv2_utils.py is using OpenCV to create the parameters.
Subsequently a web app is created with the option to input a number of images with cracks and outputs the length, width, category of the cracks along with a mask for the crack area

![image](https://user-images.githubusercontent.com/46296774/177764562-f7ed470d-22b9-4e13-b5a0-74254b54b841.png)
[23.05, 16:15] алихан: https://wvhdhfddtusppjmsgmvn.supabase.co/rest/v1/sensor_data
[23.05, 16:15] алихан: apikey: sb_publishable_rHDFfoFz_v9zDNKk9o64Gw_Pc-eZvln
Authorization: Bearer sb_publishable_rHDFfoFz_v9zDNKk9o64Gw_Pc-eZvln
Content-Type: application/json
[23.05, 16:16] алихан: {
  "temperature": 24.5,
  "humidity": 45.0,
  "air_quality": 410.0
}
[23.05, 16:16] алихан: curl -X POST 'https://wvhdhfddtusppjmsgmvn.supabase.co/rest/v1/sensor_data' \
-H "apikey: sb_publishable_rHDFfoFz_v9zDNKk9o64Gw_Pc-eZvln" \
-H "Authorization: Bearer sb_publishable_rHDFfoFz_v9zDNKk9o64Gw_Pc-eZvln" \
-H "Content-Type: application/json" \
-d '{"temperature": 25.3, "humidity": 48.1, "air_quality": 395}'
create table public.artifacts (
  id uuid not null default gen_random_uuid (),
  name text not null,
  image_path text not null default ''::text,
  model_url text null,
  local_model_path text null,
  created_at timestamp with time zone null default now(),
  status text null default 'pending'::text,
  type text null default 'Unknown'::text,
  material text null default 'Unknown'::text,
  era text null default 'Unknown'::text,
  purpose text null default 'Unknown'::text,
  condition text null default 'Stable'::text,
  crack_percentage double precision null default 0.0,
  constraint artifacts_pkey primary key (id)
) TABLESPACE pg_default;


AQ.Ab8RN6Jl-U_MS5qF4_tFP7OzoWzH0YPz1_dasTFUJ8w2-tBXVw